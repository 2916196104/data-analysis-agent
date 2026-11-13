import { writeFileSync } from 'node:fs';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { AnalysisParameters } from '../../types';
import { emptyTaskState } from '../../task-state';
import { useTask } from '../../useTask';
import { copyScope } from './followup';
import { useAgentChat } from './useAgentChat';
import captured from './__fixtures__/p7c-s2-native-post.json';

vi.mock('../../useTask', () => ({ useTask: vi.fn() }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });

it('serializes the fixed native request through React hook and api.submit without a timezone input', async () => {
  localStorage.clear();
  vi.mocked(useTask).mockReturnValue({ ...emptyTaskState, refresh: vi.fn() });
  const requests: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    requests.push({ url, init });
    return new Response(JSON.stringify({ status: 'ok', result: {
      task: { task_id: 'OFFLINE-F01-TRANSPORT', status: 'queued' }, reused: false,
    } }), { status: 202, headers: { 'Content-Type': 'application/json' } });
  }));
  const scope = copyScope(captured.parameters as AnalysisParameters);
  const hook = renderHook(() => useAgentChat());
  await act(async () => { expect(await hook.result.current.send(scope, captured.parameters.question)).toBe(true); });
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toMatch(/\/api\/v1\/tasks$/);
  expect(requests[0].init.method).toBe('POST');
  const body = JSON.parse(String(requests[0].init.body));
  expect(body.kind).toBe('analysis');
  expect(body.parameters).toEqual({ ...captured.parameters, conversation_id: expect.any(String) });
  expect(body.parameters).not.toHaveProperty('timezone');
  expect(body.parameters.selection_scope).not.toHaveProperty('timezone');
  expect(requests[0].init.headers).toMatchObject({ 'Idempotency-Key': expect.any(String) });
  if (process.env.F01_CAPTURE_POST_PATH) {
    writeFileSync(process.env.F01_CAPTURE_POST_PATH, JSON.stringify(body, null, 2) + '\n', 'utf8');
  }
});
