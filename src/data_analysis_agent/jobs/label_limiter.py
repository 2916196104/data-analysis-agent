"""Redis 原子滑动窗口：所有 Worker 共享请求数、token 预留与并发租约。"""

import hashlib
import json
import threading
import uuid

from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.reviews.prompts import BATCH_SYSTEM_PROMPT, SYSTEM_PROMPT


class QuotaRequestTooLarge(ConfigurationError):
    """当前请求无法落入配置的 TPM 窗口，需要人工调整批大小或配额。"""

# 使用 Redis TIME，消除 Worker 时钟偏差；四个键使用相同 hash tag。
ACQUIRE = """
local cfg,events,costs,active = KEYS[1],KEYS[2],KEYS[3],KEYS[4]
local clock=redis.call('TIME')
local now=tonumber(clock[1])*1000+tonumber(clock[2])/1000
local expected=ARGV[1]
if not redis.call('HGET',cfg,'signature') then
  redis.call('HSET',cfg,'signature',expected,'blocked_until',now+tonumber(ARGV[9]))
end
if redis.call('HGET',cfg,'signature') ~= expected then return {-1,0} end
local epoch=ARGV[10]
if epoch=='' then epoch=string.match(redis.call('INFO','server'),'run_id:(%w+)') end
if not epoch then return {-3,0} end
if redis.call('HGET',cfg,'server_epoch')~=epoch then
  local previous=tonumber(redis.call('HGET',cfg,'blocked_until') or 0)
  redis.call('HSET',cfg,'server_epoch',epoch,'blocked_until',math.max(previous,now+tonumber(ARGV[9])))
end
local blocked=tonumber(redis.call('HGET',cfg,'blocked_until') or 0)
if blocked>now then return {0,math.ceil(blocked-now)} end
local window=tonumber(ARGV[8])
local old=redis.call('ZRANGEBYSCORE',events,'-inf',now-window)
for _,id in ipairs(old) do redis.call('HDEL',costs,id) end
redis.call('ZREMRANGEBYSCORE',events,'-inf',now-window)
redis.call('ZREMRANGEBYSCORE',active,'-inf',now)
local used=0
for _,cost in ipairs(redis.call('HVALS',costs)) do used=used+tonumber(cost) end
local amount=tonumber(ARGV[6])
if amount>tonumber(ARGV[3]) then return {-2,0} end
if redis.call('ZCARD',events)>=tonumber(ARGV[2]) or used+amount>tonumber(ARGV[3]) then
  local first=redis.call('ZRANGE',events,0,0,'WITHSCORES')
  return {0,math.max(1,math.ceil(tonumber(first[2] or now)+window-now))}
end
if redis.call('ZCARD',active)>=tonumber(ARGV[4]) then return {0,100} end
redis.call('ZADD',events,now,ARGV[5])
redis.call('HSET',costs,ARGV[5],amount)
redis.call('ZADD',active,now+tonumber(ARGV[7]),ARGV[5])
return {1,0}
"""

RELEASE = """
redis.call('ZREM',KEYS[1],ARGV[1])
if tonumber(ARGV[2])>=0 and redis.call('HEXISTS',KEYS[2],ARGV[1])==1 then
  redis.call('HSET',KEYS[2],ARGV[1],ARGV[2])
end
return 1
"""

COOLDOWN = """
local clock=redis.call('TIME')
local now=tonumber(clock[1])*1000+tonumber(clock[2])/1000
local previous=tonumber(redis.call('HGET',KEYS[1],'blocked_until') or 0)
redis.call('HSET',KEYS[1],'blocked_until',math.max(previous,now+tonumber(ARGV[1])))
return 1
"""


class GlobalLimiter:
    def __init__(self, redis, *, scope, rpm, tpm, concurrency=2, lease_seconds=120,
                 window_ms=60000, cold_start_seconds=None, server_epoch=""):
        if min(rpm, tpm, concurrency, lease_seconds, window_ms) <= 0:
            raise ConfigurationError("共享限流配额和租约必须大于零。")
        self.redis = redis
        prefix = "label-quota:{" + hashlib.sha256(scope.encode()).hexdigest() + "}"
        self.keys = [prefix + ":" + name for name in ("config", "events", "costs", "active")]
        self.signature = json.dumps([rpm, tpm, concurrency, lease_seconds, window_ms])
        self.rpm, self.tpm, self.concurrency = rpm, tpm, concurrency
        self.lease_ms, self.window_ms = lease_seconds * 1000, window_ms
        # Redis 重启后丢失配额历史；等待一个窗口及旧请求租约后才允许继续。
        self.cold_ms = max(window_ms, self.lease_ms) if cold_start_seconds is None else cold_start_seconds * 1000
        self.active = set()
        self.lock = threading.Lock()
        self.server_epoch = server_epoch  # 协议模拟器显式提供；真实 Redis 在 Lua 中原子读取 run_id。

    def reserve(self, amount):
        token = uuid.uuid4().hex
        result, wait_ms = self.redis.eval(ACQUIRE, len(self.keys), *self.keys, self.signature,
            self.rpm, self.tpm, self.concurrency, token, amount, self.lease_ms, self.window_ms, self.cold_ms, self.server_epoch)
        if result == -1:
            raise ConfigurationError("同一 LABEL_QUOTA_SCOPE 的 Worker 限流配置不一致，请统一配置。")
        if result == -2:
            raise QuotaRequestTooLarge("单次请求 token 预留超过 LABEL_TPM，请减小 batch_size 或核对配额。")
        if result == -3:
            raise ConfigurationError("无法核实 Redis 启动标识，请检查 INFO 权限。")
        if result:
            with self.lock:
                self.active.add(token)
            return token, 0
        return None, wait_ms / 1000

    def acquire(self, tasks, stop):
        # UTF-8 字节数是保守输入预算，另留消息结构余量；输出上限与共享标注请求的 4096 一致。
        prompt = BATCH_SYSTEM_PROMPT if len(tasks) > 1 else SYSTEM_PROMPT
        amount = len(prompt.encode()) + sum(len(t["text"].encode()) for t in tasks) + 1024 + 4096
        while not stop.is_set():
            token, delay = self.reserve(amount)
            if token:
                return token
            stop.wait(min(max(delay, 0.01), 0.5))
        return None

    def release(self, token, usage):
        amount = usage.get("total_tokens", -1)
        self.redis.eval(RELEASE, 2, self.keys[3], self.keys[2], token, amount)
        with self.lock:
            self.active.discard(token)

    def heartbeat(self):
        with self.lock:
            tokens = list(self.active)
        if tokens:
            clock = self.redis.time()
            now = clock[0] * 1000 + clock[1] / 1000
            # XX 防止释放与续租交错时复活已经结束的请求。
            self.redis.zadd(self.keys[3], {token: now + self.lease_ms for token in tokens}, xx=True)

    def cooldown(self, seconds):
        self.redis.eval(COOLDOWN, 1, self.keys[0], max(0, seconds) * 1000)
