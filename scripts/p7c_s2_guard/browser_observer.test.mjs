// Node built-ins only. No Chrome, child spawn, HTTP, PostgreSQL or model calls.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createPlaywrightAdapter, ownedChildProcessHandle, runBrowserObserver } from './browser_observer.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const out = process.env.F02_BROWSER_OUT ?? path.join(repo, 'outputs/mvp-real-acceptance/p7c-s2-r1a-f02-20261113T142834Z');
const runId = `offline-tests-${new Date().toISOString().replaceAll(':', '').replaceAll('.', '')}-${process.pid}`;
const runDir = path.join(out, 'browser', runId);
mkdirSync(runDir, { recursive: true });
const rows = [];
let scenarioId = 0;

class FakeClock {
  time = 0;
  nextId = 0;
  timers = new Map();
  now = () => this.time;
  setTimeout = (fn, delay) => {
    const id = ++this.nextId;
    this.timers.set(id, { at: this.time + delay, fn });
    return id;
  };
  clearTimeout = id => this.timers.delete(id);
  async drive(promise) {
    let done = false, result, failure;
    promise.then(value => { result = value; done = true; }, error => { failure = error; done = true; });
    for (let i = 0; !done && i < 10_000; i++) {
      await new Promise(setImmediate); // Flush promise continuations before advancing virtual time.
      if (done) break;
      const next = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      assert.ok(next, 'observer hung with no cancellable timer');
      this.time = next[1].at;
      this.timers.delete(next[0]);
      next[1].fn();
    }
    assert.ok(done, 'observer exceeded finite scheduler step limit');
    if (failure) throw failure;
    return result;
  }
  async advanceTo(time) {
    while (true) {
      const next = [...this.timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > time) break;
      this.time = next[1].at;
      this.timers.delete(next[0]);
      next[1].fn();
      await new Promise(setImmediate);
    }
    this.time = Math.max(this.time, time);
    await new Promise(setImmediate);
  }
}

class MockOwnedHandle {
  owned = true;
  alive = true;
  calls = [];
  pending = new Set();
  exitListeners = new Set();
  cancelledOperations = [];
  constructor(id, role, { hangStop = false, hangTerminate = false, unverifiable = false } = {}) {
    Object.assign(this, { id, role, hangStop, hangTerminate, unverifiable });
  }
  isAlive = () => this.alive;
  onUnexpectedExit = listener => { this.exitListeners.add(listener); return () => this.exitListeners.delete(listener); };
  unexpectedExit() { this.alive = false; for (const listener of this.exitListeners) listener(); }
  waitForever(stage, ctx) {
    // Deliberately ignores AbortSignal. The owner must stop the underlying operation.
    return new Promise((resolve, reject) => this.pending.add({ stage, resolve, reject, signal: ctx?.signal }));
  }
  stopPending() {
    for (const pending of this.pending) {
      this.cancelledOperations.push({ stage: pending.stage, signal_aborted: pending.signal?.aborted });
      pending.reject(new Error(`owned handle stopped: ${pending.stage}`));
    }
    this.pending.clear();
  }
  stop = ctx => {
    this.calls.push({ method: 'stop', signal: !!ctx.signal, at: ctx.deadlineAt });
    if (this.hangStop) return this.waitForever('stop', ctx);
    this.alive = false;
    this.stopPending();
  };
  terminate = ctx => {
    this.calls.push({ method: 'terminate', signal: !!ctx.signal, at: ctx.deadlineAt });
    if (this.hangTerminate) return this.waitForever('terminate', ctx);
    this.alive = false;
    this.stopPending();
  };
  verifyStopped = async ctx => {
    this.calls.push({ method: 'verifyStopped', signal: !!ctx.signal, at: ctx.deadlineAt });
    const stopped = !this.alive && !this.unverifiable;
    return { stopped, proof: { kind: 'mock-owned-handle', resource_id: this.id, exit_observed: stopped } };
  };
}

function fixture({ scenario = 'success', hangStage, hangTerminate = false, unverifiable = false } = {}) {
  const clock = new FakeClock();
  const browser = new MockOwnedHandle('owned-browser', 'browser', { hangStop: hangStage === 'close', hangTerminate, unverifiable });
  const service = new MockOwnedHandle('owned-api-worker', 'service', { hangStop: hangStage === 'services', hangTerminate, unverifiable });
  const effects = { submits: 0, submitCalls: 0, polls: 0, prepareCalls: 0, stages: [] };
  let handlers;
  const stage = async (name, ctx, result) => {
    effects.stages.push({ name, at: clock.now(), has_signal: ctx.signal instanceof AbortSignal, deadline: ctx.deadlineAt });
    ctx.record('mock-stage', { name });
    if (name === hangStage || (scenario === 'absolute_deadline' && name === 'json'))
      return browser.waitForever(name, ctx);
    return result;
  };
  const adapter = { mode: 'OFFLINE_MOCK_NO_REAL_BUSINESS', resources: [browser, service],
    subscribeFailure(value) { handlers = value; return () => { handlers = null; }; },
    prepare(ctx) { effects.prepareCalls++; return stage('prepare', ctx, true); },
    submit(ctx) {
      effects.submitCalls++;
      return ctx.guardedSubmit(() => {
        effects.submits++;
        const response = { status: scenario === 'http500' ? 500 : 200 };
        response.json = () => adapter.json(response, ctx);
        return stage('submit', ctx, response);
      });
    },
    json: (_response, ctx) => stage('json', ctx, { result: scenario === 'task_create_failure' ? {} : { task: { task_id: 'F02-MOCK-TASK', status: 'queued' } } }),
    taskFromPost: (body, ctx) => stage('task-id', ctx, body.result.task),
    poll(id, ctx) {
      assert.equal(id, 'F02-MOCK-TASK');
      effects.polls++;
      if (scenario === 'requestfailed') handlers.fail(new Error('requestfailed'), { url: '/api/v1/tasks', error: 'net::ERR_FAILED' });
      if (scenario === 'api_exit') { service.alive = false; handlers.fail(new Error('API exited'), { exit_code: 1 }); }
      if (scenario === 'browser_interruption') { browser.alive = false; handlers.fail(new Error('browser disconnected')); }
      return stage('poll', ctx, { task_id: id, status: scenario === 'task_timeout' ? 'running' : scenario === 'worker_exception' ? 'failed' : 'succeeded',
        result: { synthetic: true } });
    },
    screenshot: ctx => stage('screenshot', ctx, Buffer.from('MOCK screenshot bytes; not a rendered page')),
    visibleText: ctx => stage('visible-text', ctx, 'MOCK visible text; not a real browser'),
    trace: ctx => stage('trace', ctx, Buffer.from('MOCK trace bytes; not a Playwright trace')) };
  const outputDir = path.join(runDir, `${++scenarioId}-${scenario}-${hangStage ?? 'normal'}`);
  return { clock, browser, service, effects, adapter, outputDir };
}

async function observe(f, options = {}) {
  const report = await f.clock.drive(runBrowserObserver({ adapter: f.adapter, outputDir: f.outputDir,
    deadlineAt: 1000, cleanupReserveMs: 200, operationTimeoutMs: 800, cleanupStepMs: 70,
    pollIntervalMs: 1, clock: f.clock, ...options }));
  assert.ok(report.elapsed_ms <= 1000, JSON.stringify(report));
  assert.ok(f.effects.submits <= 1);
  assert.equal(report.retry_attempts, 0);
  if (report.evidence.screenshot || report.raw_events_file) {
    const raw = readFileSync(path.join(f.outputDir, 'raw-events.jsonl'), 'utf8');
    assert.ok(raw.includes('observer_started'));
    assert.ok(raw.includes('observer_finished'));
    const saved = JSON.parse(readFileSync(path.join(f.outputDir, 'result.json'), 'utf8'));
    assert.equal(saved.status, report.status);
  }
  return report;
}

function checked(name, fn) {
  test(name, async () => {
    const row = { name, status: 'FAIL' };
    rows.push(row);
    try { Object.assign(row, await fn()); row.status = 'PASS'; }
    catch (error) { row.error = error.stack; throw error; }
  });
}

checked('historical source baseline was actually replayed before candidate work', async () => {
  const baseline = JSON.parse(readFileSync(path.join(out, 'browser/historical-browser-baseline.json'), 'utf8'));
  const historicalSource = readFileSync(path.join(repo, 'outputs/mvp-real-acceptance/p7c-s2-real-20261113T103342Z/browser.mjs'));
  const sha256 = createHash('sha256').update(historicalSource).digest('hex');
  assert.equal(sha256, baseline.subject_sha256);
  assert.equal(baseline.reproduction_tests, 8);
  assert.equal(baseline.reproductions_passed, 8);
  assert.equal(baseline.readiness_passed, 0);
  return { historical_sha256: sha256, historical_reproductions: 8, real_business: 'NOT RUN' };
});

for (const scenario of ['http500', 'requestfailed', 'api_exit', 'task_create_failure', 'worker_exception', 'task_timeout', 'browser_interruption', 'absolute_deadline']) {
  checked(`original failure: ${scenario}`, async () => {
    const f = fixture({ scenario });
    const report = await observe(f);
    assert.equal(report.status, 'BLOCKED');
    assert.equal(report.submission_attempts, 1);
    assert.equal(report.cleanup_verified, true);
    assert.equal(report.pending_operations.length, 0);
    assert.equal(f.browser.alive, false);
    assert.equal(f.service.alive, false);
    if (['requestfailed', 'api_exit', 'task_create_failure', 'worker_exception'].includes(scenario)) assert.ok(report.elapsed_ms < 800);
    if (scenario === 'task_create_failure') assert.equal(f.effects.polls, 0);
    if (scenario === 'absolute_deadline') assert.ok(report.missing_evidence.some(row => row.label === 'post-response'));
    return { scenario, elapsed_ms: report.elapsed_ms, observation_status: report.status, submit_attempts: f.effects.submits,
      cleanup_verified: report.cleanup_verified, result_file: path.relative(out, path.join(f.outputDir, 'result.json')) };
  });
}

for (const hangStage of ['prepare', 'submit', 'json', 'task-id', 'poll', 'screenshot', 'visible-text', 'trace', 'close', 'services']) {
  checked(`hanging underlying promise: ${hangStage}`, async () => {
    const f = fixture({ hangStage });
    const report = await observe(f);
    assert.equal(report.status, 'BLOCKED');
    if (['close', 'services'].includes(hangStage)) {
      const owner = hangStage === 'close' ? f.browser : f.service;
      assert.ok(owner.calls.some(row => row.method === 'terminate'));
      assert.ok(report.cleanup.some(row => row.phase === 'stop' && row.completed === false));
      // Resource cleanup recovered, but a close timeout must never become observer PASS.
      assert.equal(report.readiness_status, 'BLOCKED');
    } else {
      assert.equal(report.status, 'BLOCKED');
      assert.ok(report.missing_evidence.length > 0);
      const pendingOwner = [...f.browser.calls, ...f.service.calls];
      assert.ok(pendingOwner.some(row => row.method === 'stop'));
    }
    assert.equal(report.cleanup_verified, true);
    assert.equal(report.pending_operations.length, 0);
    assert.equal(f.browser.pending.size + f.service.pending.size, 0);
    assert.equal(f.browser.alive || f.service.alive, false);
    assert.ok(f.effects.stages.every(row => row.has_signal));
    assert.ok([...f.browser.cancelledOperations, ...f.service.cancelledOperations].some(row => row.signal_aborted === true));
    return { stage: hangStage, elapsed_ms: report.elapsed_ms, observation_status: report.status, readiness_status: report.readiness_status,
      submit_attempts: f.effects.submits, owned_handles_stopped: true, result_file: path.relative(out, path.join(f.outputDir, 'result.json')) };
  });
}

checked('late prepare completion cannot invoke submit after timeout', async () => {
  const f = fixture();
  f.adapter.prepare = ctx => new Promise(resolve => { f.clock.setTimeout(() => resolve(true), 850); });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  await f.clock.advanceTo(1200);
  assert.equal(f.effects.submitCalls, 0);
  assert.equal(f.effects.submits, 0);
  assert.equal(report.submission_attempts, 0);
  return { submit_attempts_after_late_completion: 0, observation_status: report.status };
});

checked('late submit callback is refused even when it ignores AbortSignal', async () => {
  const f = fixture();
  let lateDenials = 0;
  f.adapter.submit = ctx => {
    f.clock.setTimeout(() => {
      try { ctx.guardedSubmit(() => { f.effects.submits++; }); }
      catch (error) { assert.match(error.message, /forbidden after/); lateDenials++; }
    }, 850);
    return f.browser.waitForever('late-submit', ctx);
  };
  const report = await observe(f);
  await f.clock.advanceTo(1200);
  assert.equal(lateDenials, 1);
  assert.equal(f.effects.submits, 0);
  assert.equal(report.submission_attempts, 0);
  return { observation_status: report.status, late_submit_denials: 1, submit_attempts: 0 };
});

checked('second guarded submit is refused and consumed quota is never refunded', async () => {
  const f = fixture();
  f.adapter.submit = ctx => {
    const response = ctx.guardedSubmit(() => { f.effects.submits++; return { status: 200 }; });
    assert.throws(() => ctx.guardedSubmit(() => { f.effects.submits++; }), /second submission forbidden/);
    return response;
  };
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(f.effects.submits, 1);
  return { submit_attempts: 1, retry_attempts: 0, observation_status: report.status };
});

checked('unconfirmed owned cleanup stays BLOCKED', async () => {
  const f = fixture({ hangStage: 'close', hangTerminate: true, unverifiable: true });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(report.cleanup_verified, false);
  assert.ok(report.pending_operations.length > 0);
  assert.ok(report.failures.some(row => row.includes('cleanup could not be confirmed')));
  return { cleanup_verified: false, observation_status: report.status, unresolved_operations: report.pending_operations.length };
});

checked('missing ownership controller blocks before prepare and submit', async () => {
  const f = fixture();
  f.adapter.resources = [];
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(f.effects.prepareCalls, 0);
  assert.equal(f.effects.submits, 0);
  return { observation_status: report.status, prepare_attempts: 0, submit_attempts: 0 };
});

checked('wrong task_id from poll blocks without a second submit', async () => {
  const f = fixture();
  f.adapter.poll = async () => ({ task_id: 'OTHER-TASK', status: 'succeeded' });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.ok(report.failures.some(row => row.includes('different task_id')));
  return { observation_status: report.status, submit_attempts: f.effects.submits };
});

checked('a reused output directory is refused without overwriting prior evidence', async () => {
  const first = fixture();
  await observe(first);
  const before = readFileSync(path.join(first.outputDir, 'result.json'));
  const second = fixture();
  second.outputDir = first.outputDir;
  const report = await second.clock.drive(runBrowserObserver({ adapter: second.adapter, outputDir: second.outputDir,
    deadlineAt: 1000, cleanupReserveMs: 200, operationTimeoutMs: 800, cleanupStepMs: 70, clock: second.clock }));
  assert.equal(report.status, 'BLOCKED');
  assert.equal(second.effects.prepareCalls, 0);
  assert.equal(second.effects.submits, 0);
  assert.deepEqual(readFileSync(path.join(first.outputDir, 'result.json')), before);
  return { observation_status: report.status, original_evidence_preserved: true, submit_attempts: 0 };
});

checked('hanging exit verification cannot be mistaken for cleanup proof', async () => {
  const f = fixture();
  f.browser.verifyStopped = ctx => f.browser.waitForever('verifyStopped', ctx);
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(report.cleanup_verified, false);
  assert.ok(report.cleanup.some(row => row.phase === 'verifyStopped' && !row.completed));
  return { observation_status: report.status, cleanup_verified: false, elapsed_ms: report.elapsed_ms };
});

checked('one absolute deadline includes prepare time; no fresh submit deadline', async () => {
  const f = fixture({ hangStage: 'json' });
  f.adapter.prepare = ctx => new Promise(resolve => { f.clock.setTimeout(() => resolve(true), 700); });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(report.elapsed_ms, 800);
  assert.equal(report.deadline_at_ms, 1000);
  return { prepare_ms: 700, total_ms: report.elapsed_ms, absolute_deadline_ms: 1000 };
});

checked('deadline over 900 seconds is rejected before prepare', async () => {
  const f = fixture();
  const report = await f.clock.drive(runBrowserObserver({ adapter: f.adapter, outputDir: f.outputDir, deadlineAt: 900_001, clock: f.clock }));
  assert.equal(report.status, 'BLOCKED');
  assert.equal(f.effects.prepareCalls, 0);
  assert.equal(f.effects.submits, 0);
  return { observation_status: report.status, submit_attempts: 0 };
});

checked('success is observer protocol only; actual OS/browser readiness remains BLOCKED', async () => {
  const f = fixture();
  const report = await observe(f);
  assert.equal(report.status, 'PASS');
  assert.equal(report.readiness_status, 'BLOCKED');
  assert.equal(report.resource_boundary_status, 'BLOCKED');
  assert.equal(report.missing_evidence.length, 0);
  assert.equal(report.cleanup_verified, true);
  return { observation_status: 'PASS', readiness_status: 'BLOCKED', actual_os_or_browser_proof: 'NOT RUN', submit_attempts: 1 };
});

checked('Playwright adapter uses supplied page/context and owned resources only', async () => {
  const page = new EventEmitter();
  const browserEvents = new EventEmitter();
  page.screenshot = async () => Buffer.from('mock-png');
  page.locator = () => ({ innerText: async () => 'mock-body' });
  const context = { browser: () => browserEvents, tracing: { stop: async () => Buffer.from('mock-trace') } };
  const f = fixture();
  f.adapter = createPlaywrightAdapter({ page, context, browserController: f.browser, serviceControllers: [f.service],
    prepare: f.adapter.prepare, submit: f.adapter.submit, poll: f.adapter.poll });
  const report = await observe(f);
  assert.equal(report.status, 'PASS');
  assert.equal(report.readiness_status, 'BLOCKED');
  assert.equal(page.listenerCount('requestfailed'), 0);
  assert.equal(browserEvents.listenerCount('disconnected'), 0);
  return { observation_status: report.status, readiness_status: report.readiness_status, actual_playwright: 'NOT RUN' };
});

checked('Playwright requestfailed event cancels the active operation immediately', async () => {
  const page = new EventEmitter();
  const browserEvents = new EventEmitter();
  page.screenshot = async () => Buffer.from('mock-png');
  page.locator = () => ({ innerText: async () => 'mock-body' });
  const context = { browser: () => browserEvents, tracing: { stop: async () => Buffer.from('mock-trace') } };
  const f = fixture();
  f.adapter = createPlaywrightAdapter({ page, context, browserController: f.browser, serviceControllers: [f.service],
    prepare: f.adapter.prepare, submit: f.adapter.submit,
    poll: async (_id, ctx) => { page.emit('requestfailed', { url: () => 'http://mock.invalid/tasks', failure: () => ({ errorText: 'synthetic' }) }); return f.browser.waitForever('poll-event', ctx); } });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(report.elapsed_ms, 1);
  assert.equal(report.cleanup_verified, true);
  return { observation_status: report.status, elapsed_ms: report.elapsed_ms, real_request: 'NOT RUN' };
});

checked('Playwright supplied service exit cancels JSON decoding before its timeout', async () => {
  const page = new EventEmitter();
  page.screenshot = async () => Buffer.from('mock-png');
  page.locator = () => ({ innerText: async () => 'mock-body' });
  const context = { browser: () => new EventEmitter(), tracing: { stop: async () => Buffer.from('mock-trace') } };
  const f = fixture();
  const submit = ctx => ctx.guardedSubmit(() => {
    f.effects.submits++;
    return { status: 200, json: () => {
      f.clock.setTimeout(() => f.service.unexpectedExit(), 10);
      return f.browser.waitForever('json-service-exit', ctx);
    } };
  });
  f.adapter = createPlaywrightAdapter({ page, context, browserController: f.browser, serviceControllers: [f.service],
    prepare: f.adapter.prepare, submit, poll: f.adapter.poll });
  const report = await observe(f);
  assert.equal(report.status, 'BLOCKED');
  assert.equal(report.elapsed_ms, 10);
  assert.equal(f.service.exitListeners.size, 0);
  assert.equal(report.cleanup_verified, true);
  return { observation_status: report.status, elapsed_ms: report.elapsed_ms, real_service: 'NOT RUN' };
});

checked('owned-process helper rejects arbitrary PID-like objects without spawning', async () => {
  assert.throws(() => ownedChildProcessHandle({ pid: 1234, kill() { throw new Error('must never kill'); } }, { id: 'unowned', role: 'browser' }), /already-owned ChildProcess/);
  return { process_spawns: 0, process_kills: 0, actual_os_exit_proof: 'NOT RUN' };
});

after(() => {
  const summary = { stage: 'P7-C-S2-R1A-F02', status: 'BLOCKED',
    offline_test_status: rows.every(row => row.status === 'PASS') ? 'PASS' : 'FAIL',
    test_count: rows.length, passed: rows.filter(row => row.status === 'PASS').length,
    failed: rows.filter(row => row.status === 'FAIL').length, original_failure_scenarios: 8,
    hanging_stage_scenarios: 10, real_browser_test: 'NOT RUN', real_services_test: 'NOT RUN', actual_os_process_tree_isolation: 'NOT RUN',
    actual_network_isolation: 'NOT RUN', real_business_posts: 0, process_spawns: 0,
    historical_baseline: 'browser/historical-browser-baseline.json',
    source_files: ['scripts/p7c_s2_guard/browser_observer.mjs', 'scripts/p7c_s2_guard/browser_observer.test.mjs'].map(file => ({ path: file,
      sha256: createHash('sha256').update(readFileSync(path.join(repo, file))).digest('hex') })),
    limitations: ['Mock owner cleanup proves only the adapter contract, not actual OS process-tree or network isolation.',
      'The supplied existing ChildProcess wrapper observes only that process exit; descendants require an independently verified ownership boundary.',
      'No real Chrome, API, Worker, PostgreSQL, model or paid call was run.',
      'Local evidence filesystem I/O and event-loop starvation require an external owned supervisor for a physical hard-deadline guarantee; that OS boundary is NOT RUN.'],
    test_run_directory: path.relative(out, runDir), cases: rows };
  writeFileSync(path.join(runDir, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
  writeFileSync(path.join(out, 'browser-failure-tests.json'), JSON.stringify(summary, null, 2) + '\n');
});
