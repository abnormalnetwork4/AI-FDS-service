import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeEvent, eventLevel, nullableScore, fetchEventPage } from './events.js';

test('missing values never become safe, zero confidence or fabricated contributions', () => {
  const row = normalizeEvent({ id: 'capture', network_reasons: [{ code: 'N1', status: 'pending' }] });
  assert.equal(eventLevel(row), 'pending');
  assert.equal(row.score, null);
  assert.equal(row.confidence, null);
  assert.equal(row.network_reasons[0].weight, null);
  assert.equal(row.network_reasons[0].score, null);
});

test('zero is a valid completed finding score, not a fused safety verdict', () => {
  const row = normalizeEvent({ id: 'capture', score: 0, prompt_reasons: [{ code: 'x', status: 'complete', score: 0 }] });
  assert.equal(row.prompt_reasons[0].score, 0);
  assert.equal(row.prompt_reasons[0].status, 'complete');
  assert.equal(eventLevel(row), 'pending');
  assert.equal(row.score, null);
  for (const value of [null, undefined, '', '0', NaN, -1, 101]) assert.equal(nullableScore(value), null);
});

test('errors and unrecognized finding states remain explicit', () => {
  const row = normalizeEvent({ id: 'capture', status: 'error', network_reasons: [{ status: 'unexpected', score: 0 }] });
  assert.equal(eventLevel(row), 'error');
  assert.equal(row.network_reasons[0].status, 'pending');
  assert.equal(row.network_reasons[0].score, null);
});

test('captured duration uses both recorded timestamps', () => {
  const row = normalizeEvent({ id: 'capture', started_at: '2026-09-28T00:00:00Z', ended_at: '2026-09-28T00:05:00Z' });
  assert.equal(row.endedAtMs - row.startedAtMs, 300000);
});

test('API uses bounded recent page and keeps total, errors never fall back to mock', async () => {
  const page = await fetchEventPage('', undefined, async (url) => {
    assert.equal(url, '/api/v1/dashboard/events?limit=200');
    return { ok: true, json: async () => ({ events: [{ id: 'real' }], total: 300 }) };
  });
  assert.equal(page.events[0].id, 'real');
  assert.equal(page.total, 300);
  await assert.rejects(fetchEventPage('', undefined, async () => ({ ok: false, status: 503 })), /503/);
  await assert.rejects(fetchEventPage('', undefined, async () => ({ ok: true, json: async () => ({}) })), /응답/);
});
