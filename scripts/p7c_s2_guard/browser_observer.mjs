// F02 candidate only. Importing this module starts no browser, service, process or network call.
import { appendFileSync, existsSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { ChildProcess } from 'node:child_process';

const realClock = { now: Date.now, setTimeout, clearTimeout };
const errorText = error => error instanceof Error ? `${error.name}: ${error.message}` : String(error);
const requiredEvidence = ['post-response', 'terminal-task', 'screenshot', 'visible-text', 'trace'];

/**
 * Offline-testable observer, not execution authorization. Adapters must expose owned browser
 * and service handles BEFORE prepare. Each handle implements
 * isAlive/onUnexpectedExit/stop/terminate/verifyStopped.
 * A Promise timeout closes the submit gate and aborts its signal; the independent handles then
 * stop the underlying resources. Unconfirmed cleanup always produces BLOCKED.
 *
 * One deadline (at most 900 s) includes prepare, submit, decoding, polling, evidence and cleanup.
 * The supplied clock exists for deterministic offline tests, never to extend a real run.
 */
export async function runBrowserObserver({ adapter, outputDir, deadlineAt,
  cleanupReserveMs = 10_000, operationTimeoutMs = 15_000, cleanupStepMs = 2_000,
  pollIntervalMs = 250, clock = realClock }) {
  const startedAt = clock.now();
  const absoluteDeadline = deadlineAt ?? startedAt + 900_000;
  const report = { status: 'BLOCKED', mode: adapter?.mode ?? 'UNVERIFIED_ADAPTER',
    started_at_ms: startedAt, deadline_at_ms: absoluteDeadline, submission_attempts: 0,
    retry_attempts: 0, task_id: null, failures: [], evidence: {}, missing_evidence: [],
    cleanup: [], raw_events_file: 'raw-events.jsonl', resource_boundary_status: 'BLOCKED' };
  const workAbort = new AbortController();
  const operations = new Map();
  let workDeadline, submissionClosed = false, operationId = 0, dispose = () => {};
  let rawWritable = false;
  const record = (type, data = {}) => {
    const event = { at_ms: clock.now(), type, data };
    if (rawWritable) {
      try { appendFileSync(path.join(outputDir, 'raw-events.jsonl'), JSON.stringify(event) + '\n'); }
      catch (error) {
        rawWritable = false;
        report.failures.push(`raw evidence write: ${errorText(error)}`);
        closeSubmission('raw evidence unavailable');
      }
    }
    return event;
  };
  function closeSubmission(reason) {
    submissionClosed = true;
    if (!workAbort.signal.aborted) workAbort.abort(new Error(reason));
  }
  function fail(error, raw) {
    const message = errorText(error);
    report.failures.push(message);
    record('failure', { message, stack: error instanceof Error ? error.stack : undefined, raw });
    closeSubmission(message);
  }
  const context = (signal, cutoff) => ({ signal, deadlineAt: cutoff,
    remainingMs: () => Math.max(0, cutoff - clock.now()), record });
  async function bounded(name, action, cutoff, parentSignal, limit = operationTimeoutMs) {
    const remaining = Math.min(limit, cutoff - clock.now());
    if (remaining <= 0) throw new Error(`${name}: absolute deadline exhausted`);
    if (parentSignal?.aborted) throw parentSignal.reason;
    const controller = new AbortController();
    const id = ++operationId;
    const entry = { name, settled: false, aborted: false };
    operations.set(id, entry);
    let timer, abortListener;
    const cancellation = new Promise((_, reject) => {
      const abort = reason => {
        entry.aborted = true;
        controller.abort(reason);
        reject(reason);
      };
      timer = clock.setTimeout(() => abort(new Error(`${name}: deadline/operation timeout`)), remaining);
      abortListener = () => abort(parentSignal.reason ?? new Error(`${name}: cancelled`));
      parentSignal?.addEventListener('abort', abortListener, { once: true });
    });
    // Catch the underlying promise too: a late rejection must never become unhandled.
    const underlying = Promise.resolve().then(() => {
      if (controller.signal.aborted || clock.now() >= cutoff) throw new Error(`${name}: cancelled before start`);
      return action(context(controller.signal, cutoff));
    }).then(value => { entry.settled = true; return value; }, error => { entry.settled = true; throw error; });
    try {
      const value = await Promise.race([underlying, cancellation]);
      if (controller.signal.aborted || clock.now() >= cutoff) throw new Error(`${name}: completed after deadline`);
      return value;
    } finally {
      clock.clearTimeout(timer);
      parentSignal?.removeEventListener('abort', abortListener);
    }
  }
  function save(label, filename, data) {
    if (clock.now() >= absoluteDeadline) throw new Error(`${label}: no time to save evidence`);
    writeFileSync(path.join(outputDir, filename), data, { flag: 'wx' });
    if (clock.now() >= absoluteDeadline) throw new Error(`${label}: evidence write exceeded deadline`);
    report.evidence[label] = filename;
    record('evidence_saved', { label, filename });
  }
  function saveJson(label, filename, data) { save(label, filename, JSON.stringify(data, null, 2) + '\n'); }
  function checkResources() {
    for (const resource of adapter.resources) {
      if (!resource.isAlive()) throw new Error(`owned resource exited: ${resource.id}`);
    }
  }
  // This callback is the only submit entry. Consumption occurs before the effect and is never refunded.
  function guardedSubmit(effect) {
    if (submissionClosed || workAbort.signal.aborted || clock.now() >= workDeadline) {
      record('submission_denied', { reason: 'cancelled/deadline' });
      throw new Error('submission forbidden after cancellation/deadline');
    }
    if (report.submission_attempts !== 0) {
      fail(new Error('second submission forbidden'));
      throw new Error('second submission forbidden');
    }
    checkResources();
    if (typeof effect !== 'function') throw new Error('submission effect required');
    report.submission_attempts = 1;
    record('submit_attempt_consumed');
    if (!rawWritable) throw new Error('submission forbidden: raw evidence unavailable');
    return effect(context(workAbort.signal, workDeadline));
  }
  try {
    if (!Number.isFinite(absoluteDeadline) || absoluteDeadline <= startedAt || absoluteDeadline - startedAt > 900_000)
      throw new Error('absolute deadline must be within 900 seconds of observer start');
    for (const [name, value] of Object.entries({ cleanupReserveMs, operationTimeoutMs, cleanupStepMs, pollIntervalMs }))
      if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} must be positive`);
    if (cleanupReserveMs >= absoluteDeadline - startedAt) throw new Error('cleanup reserve must fit inside total deadline');
    workDeadline = absoluteDeadline - cleanupReserveMs;
    mkdirSync(outputDir); // An existing run directory cannot be reused or overwritten.
    writeFileSync(path.join(outputDir, 'raw-events.jsonl'), '', { flag: 'wx' });
    rawWritable = true;
    record('observer_started', { absoluteDeadline, workDeadline, cleanupReserveMs });
    if (!Array.isArray(adapter?.resources) || adapter.resources.length === 0)
      throw new Error('owned browser/service resource handles required before prepare');
    const ids = new Set();
    for (const resource of adapter.resources) {
      if (!resource || resource.owned !== true || typeof resource.id !== 'string' || !resource.id || ids.has(resource.id) ||
        !['isAlive', 'onUnexpectedExit', 'stop', 'terminate', 'verifyStopped'].every(method => typeof resource[method] === 'function'))
        throw new Error('invalid/duplicate owned resource handle');
      ids.add(resource.id);
    }
    if (!adapter.resources.some(resource => resource.role === 'browser')) throw new Error('owned browser controller required');
    if (!adapter.resources.some(resource => resource.role === 'service')) throw new Error('owned service controller required');
    checkResources();
    const disposers = [];
    dispose = () => { for (const off of disposers) off(); };
    for (const resource of adapter.resources) {
      const off = resource.onUnexpectedExit(() => fail(new Error(`owned resource unexpectedly exited: ${resource.id}`)));
      if (typeof off !== 'function') throw new Error('resource exit subscription must return a synchronous disposer');
      disposers.push(off);
    }
    const offAdapter = adapter.subscribeFailure?.({ fail, record });
    if (offAdapter !== undefined) {
      if (typeof offAdapter !== 'function') throw new Error('failure subscription must return a synchronous disposer');
      disposers.push(offAdapter);
    }
    await bounded('prepare', ctx => adapter.prepare(ctx), workDeadline, workAbort.signal);
    const response = await bounded('submit', ctx => adapter.submit({ ...ctx, guardedSubmit }), workDeadline, workAbort.signal);
    if (report.submission_attempts !== 1) throw new Error('adapter returned without one guarded submit');
    const status = typeof response.status === 'function' ? response.status() : response.status;
    record('post_response_metadata', { status });
    if (!Number.isInteger(status) || status < 200 || status >= 300) throw new Error(`HTTP ${status}`);
    const body = await bounded('json', ctx => adapter.json ? adapter.json(response, ctx) : response.json(), workDeadline, workAbort.signal);
    saveJson('post-response', 'post-response.json', body);
    let task = await bounded('task-id', ctx => adapter.taskFromPost ? adapter.taskFromPost(body, ctx) : body?.result?.task,
      workDeadline, workAbort.signal);
    if (typeof task?.task_id !== 'string' || !task.task_id.trim()) throw new Error('POST response missing task_id');
    report.task_id = task.task_id;
    record('task_bound', { task_id: report.task_id });
    while (true) {
      checkResources();
      if (task.task_id !== report.task_id) throw new Error('poll returned a different task_id');
      record('task_observed', task);
      if (['failed', 'partial', 'cancelled', 'timed_out'].includes(task.status)) throw new Error(`task terminal failure: ${task.status}`);
      if (task.status === 'succeeded') break;
      if (!['queued', 'pending', 'running'].includes(task.status)) throw new Error(`unknown task status: ${task.status}`);
      await bounded('poll-delay', ctx => new Promise((resolve, reject) => {
        const timer = clock.setTimeout(resolve, Math.min(pollIntervalMs, ctx.remainingMs()));
        ctx.signal.addEventListener('abort', () => { clock.clearTimeout(timer); reject(ctx.signal.reason); }, { once: true });
      }), workDeadline, workAbort.signal);
      task = await bounded('poll', ctx => adapter.poll(report.task_id, ctx), workDeadline, workAbort.signal);
    }
    saveJson('terminal-task', 'terminal-task.json', task);
    if (adapter.validateTask) {
      const valid = await bounded('validate-task', ctx => adapter.validateTask(task, ctx), workDeadline, workAbort.signal);
      if (valid === false) throw new Error('terminal task validation rejected');
    }
  } catch (error) { fail(error); }
  finally {
    closeSubmission('business observation finished; submission permanently closed');
    // Evidence has its own cancellation signal after failure, but shares the same work cutoff.
    for (const [label, filename, method] of [['screenshot', 'screenshot.png', 'screenshot'],
      ['visible-text', 'visible.txt', 'visibleText'], ['trace', 'trace.zip', 'trace']]) {
      try {
        if (!rawWritable || !workDeadline || typeof adapter?.[method] !== 'function') throw new Error('evidence unavailable');
        const file = path.join(outputDir, filename);
        const data = await bounded(label, ctx => adapter[method]({ ...ctx, path: file }), workDeadline);
        if (data !== undefined) save(label, filename, data);
        else if (existsSync(file) && statSync(file).size > 0) report.evidence[label] = filename;
        else throw new Error('adapter produced no artifact');
      } catch (error) {
        const reason = errorText(error);
        report.missing_evidence.push({ label, reason });
        report.failures.push(`${label}: ${reason}`);
        record('evidence_missing', { label, reason });
      }
    }
    try { dispose(); } catch (error) { fail(error); }
    const resources = (Array.isArray(adapter?.resources) ? adapter.resources : []).filter(resource => resource?.owned === true &&
      ['stop', 'terminate', 'verifyStopped'].every(method => typeof resource[method] === 'function'));
    const cleanupDeadline = absoluteDeadline - Math.min(500, cleanupReserveMs / 10);
    const cleanup = async (phase, handles, budget) => {
      const cutoff = Math.min(cleanupDeadline, clock.now() + Math.max(0, budget));
      return Promise.all(handles.map(async resource => {
        const row = { resource_id: resource.id, phase, completed: false };
        report.cleanup.push(row);
        try {
          row.result = await bounded(`cleanup.${phase}.${resource.id}`, ctx => resource[phase](ctx), cutoff, undefined, cleanupStepMs);
          row.completed = true;
        } catch (error) {
          row.error = errorText(error);
          report.failures.push(`cleanup.${phase}.${resource.id}: ${row.error}`);
        }
        record('cleanup_step', row);
        return { resource, row };
      }));
    };
    // Every resource gets a chance even if another close/stop hangs. No PID-name enumeration or blanket kills.
    await cleanup('stop', resources, (cleanupDeadline - clock.now()) / 4);
    const verification = await cleanup('verifyStopped', resources, (cleanupDeadline - clock.now()) / 3);
    const validProof = ({ resource, row }) => row.completed && row.result?.stopped === true &&
      row.result?.proof?.resource_id === resource.id && row.result.proof.exit_observed === true &&
      ['mock-owned-handle', 'owned-process-exit'].includes(row.result.proof.kind);
    const unconfirmed = verification.filter(result => !validProof(result)).map(result => result.resource);
    await cleanup('terminate', unconfirmed, (cleanupDeadline - clock.now()) / 2);
    const finalVerification = await cleanup('verifyStopped', resources, cleanupDeadline - clock.now());
    report.cleanup_verified = resources.length > 0 && finalVerification.every(validProof);
    // Mock proof and a single process exit cannot prove Chrome descendant/process-tree isolation.
    report.resource_boundary_status = 'BLOCKED';
    report.resource_boundary_limitation = 'OS process-tree/network isolation not verified by F02 offline tests; single-process exit is insufficient';
    report.pending_operations = [...operations.values()].filter(entry => !entry.settled);
    if (!report.cleanup_verified) report.failures.push('owned resource cleanup could not be confirmed');
    if (report.pending_operations.length) report.failures.push('underlying operations still unsettled after cleanup');
    for (const label of requiredEvidence) {
      if (!report.evidence[label] && !report.missing_evidence.some(item => item.label === label))
        report.missing_evidence.push({ label, reason: 'not reached before failure/deadline' });
    }
    report.finished_at_ms = clock.now();
    report.elapsed_ms = report.finished_at_ms - startedAt;
    if (report.finished_at_ms > absoluteDeadline) report.failures.push('total absolute deadline exceeded');
    report.status = report.failures.length === 0 && report.cleanup_verified && rawWritable ? 'PASS' : 'BLOCKED';
    report.readiness_status = report.status === 'PASS' && report.resource_boundary_status === 'PASS' ? 'PASS' : 'BLOCKED';
    record('observer_finished', { status: report.status, readiness_status: report.readiness_status });
    if (!rawWritable) report.status = report.readiness_status = 'BLOCKED';
    if (rawWritable) {
      try { writeFileSync(path.join(outputDir, 'result.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' }); }
      catch (error) {
        report.status = report.readiness_status = 'BLOCKED';
        report.failures.push(`result save: ${errorText(error)}`);
        report.missing_evidence.push({ label: 'result', reason: errorText(error) });
        record('evidence_missing', { label: 'result', reason: errorText(error) });
      }
    }
  }
  return report;
}

/** Wrap an explicitly owned ChildProcess, never an arbitrary PID or process-name match.
 * No process is created here. F02 only tests mock handles; actual OS exit proof is NOT RUN.
 */
export function ownedChildProcessHandle(child, { id, role, gracefulStop } = {}) {
  if (!(child instanceof ChildProcess) || !Number.isInteger(child.pid) || child.pid <= 0)
    throw new Error('an already-owned ChildProcess handle with pid is required');
  let exitObserved = Number.isInteger(child.exitCode) || typeof child.signalCode === 'string';
  child.once('exit', () => { exitObserved = true; });
  return { id, role, owned: true,
    isAlive: () => !exitObserved,
    onUnexpectedExit(listener) {
      child.on('exit', listener);
      return () => child.off('exit', listener);
    },
    stop: async ctx => { if (!exitObserved) { if (gracefulStop) await gracefulStop(ctx); else child.kill('SIGTERM'); } },
    terminate: async () => { if (!exitObserved) child.kill('SIGKILL'); },
    verifyStopped: async () => ({ stopped: exitObserved, proof: { kind: 'owned-process-exit',
      resource_id: id, pid: child.pid, exit_observed: exitObserved, exit_code: child.exitCode, signal: child.signalCode } }) };
}

/** Reuses supplied Playwright page/context and owned process controllers; does not import/launch Playwright.
 * prepare/submit/poll are the actual UI-specific operations. submit MUST call guardedSubmit
 * immediately around its sole UI click, never return an unguarded POST. Cancellation additionally
 * relies on the supplied owned browser controller; AbortSignal alone does not cancel Playwright.
 * Physical resource/network isolation remains unverified until separately authorized real tests.
 */
export function createPlaywrightAdapter({ page, context, browserController, serviceControllers,
  prepare, submit, poll, validateTask }) {
  const resources = [browserController, ...(serviceControllers ?? [])];
  return { mode: 'PLAYWRIGHT_CANDIDATE_NOT_AUTHORIZATION', resources, prepare, submit, poll, validateTask,
    subscribeFailure({ fail, record }) {
      const failed = request => fail(new Error('requestfailed'), { url: request.url(), error: request.failure() });
      const pageError = error => fail(error);
      const closed = () => fail(new Error('browser/page interrupted'));
      const response = value => {
        const raw = { url: value.url(), status: value.status(), method: value.request().method() };
        record('http_response', raw);
        if (raw.status >= 400) fail(new Error(`HTTP ${raw.status}`), raw);
      };
      const browser = context.browser();
      page.on('requestfailed', failed); page.on('pageerror', pageError); page.on('close', closed); page.on('response', response);
      browser?.on('disconnected', closed);
      return () => {
        page.off('requestfailed', failed); page.off('pageerror', pageError); page.off('close', closed); page.off('response', response);
        browser?.off('disconnected', closed);
      };
    },
    screenshot: ctx => page.screenshot({ fullPage: true, animations: 'disabled', timeout: Math.max(1, ctx.remainingMs()) }),
    visibleText: ctx => page.locator('body').innerText({ timeout: Math.max(1, ctx.remainingMs()) }),
    trace: ctx => context.tracing.stop({ path: ctx.path }) };
}
