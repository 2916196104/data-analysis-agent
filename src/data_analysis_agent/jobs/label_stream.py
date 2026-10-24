"""Redis Streams 只传任务 ID；消息确认始终发生在数据库提交之后。"""

from redis import Redis, ResponseError


class LabelStream:
    def __init__(self, redis, *, name="review-labels", group="label-workers", reclaim_ms=120000, max_backlog=10000):
        self.redis, self.name, self.group = redis, name, group
        self.reclaim_ms = reclaim_ms
        self.max_backlog = max_backlog
        self.cursor = "0-0"

    def ensure_group(self):
        try:
            self.redis.xgroup_create(self.name, self.group, id="0-0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def send(self, task_id):
        self.ensure_group()
        return self.redis.xadd(self.name, {"task_id": task_id})

    def receive(self, worker_id, count):
        self.ensure_group()
        claimed = self.redis.xautoclaim(self.name, self.group, worker_id, self.reclaim_ms,
                                       start_id=self.cursor, count=count)
        self.cursor = claimed[0]
        messages = list(claimed[1])
        if len(messages) < count:
            rows = self.redis.xreadgroup(self.group, worker_id, {self.name: ">"}, count=count - len(messages))
            for _, entries in rows:
                messages.extend(entries)
        return [(mid, fields["task_id"]) for mid, fields in messages]

    def ack(self, message_ids):
        if message_ids:
            # XDEL 回收已处理消息，不裁剪未消费或 pending 消息。
            with self.redis.pipeline(transaction=True) as pipe:
                pipe.xack(self.name, self.group, *message_ids)
                pipe.xdel(self.name, *message_ids)
                pipe.execute()

    def health(self):
        self.ensure_group()
        pending = self.redis.xpending(self.name, self.group)["pending"]
        groups = self.redis.xinfo_groups(self.name)
        group = next(g for g in groups if g["name"] == self.group)
        return {"stream_entries": self.redis.xlen(self.name), "pending_messages": pending,
                "undelivered_messages": group.get("lag")}


def redis_client(url):
    return Redis.from_url(url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5,
                          retry_on_timeout=False)
