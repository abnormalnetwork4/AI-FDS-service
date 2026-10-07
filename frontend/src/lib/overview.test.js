import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeEvent } from './events.js';
import { MAX_SERIES, assignColors, buildMatrix, buildRows, defaultSelection, rankUsers, timeTicks } from './overview.js';

const T0 = Date.parse('2026-10-01T01:00:00Z');
const w = (user, minute, grade, score, extra = {}) => normalizeEvent({
  id: `${user}-${minute}-${extra.device_id ?? 'pc'}`, scope: 'user_device', user, device_id: 'pc', grade, score,
  started_at: new Date(T0 + minute * 60000).toISOString(), window_start: new Date(T0 + minute * 60000).toISOString(), ...extra,
});

test('users are ranked by worst grade then score, pending is not treated as normal', () => {
  const events = [w('u1', 0, 'normal', 0), w('u2', 0, 'danger', 50, { override: true }), w('u3', 5, 'warning', 60),
    w('u4', 5, null, null), w('u1', 5, 'caution', 40)];
  assert.deepEqual(rankUsers(events).map((u) => u.user), ['u2', 'u3', 'u1', 'u4']);
  assert.equal(rankUsers(events)[3].grade, null);
});

test('default selection caps at eight lines and colours stay with the user when others toggle', () => {
  const events = Array.from({ length: 10 }, (_, i) => w(`user-${String(i + 1).padStart(2, '0')}`, 0, i < 2 ? 'danger' : 'normal', i < 2 ? 80 : i));
  const selected = defaultSelection(events);
  assert.equal(selected.length, MAX_SERIES);
  assert.ok(selected.includes('user-01') && selected.includes('user-02'));
  const colors = assignColors(selected);
  assert.equal(new Set(Object.values(colors)).size, MAX_SERIES);
  const kept = assignColors(selected.filter((u) => u !== 'user-01'), colors);
  for (const u of Object.keys(kept)) assert.equal(kept[u], colors[u]); // 남은 사용자 색 유지
  assert.equal(Object.keys(assignColors([...selected, 'extra'])).length, MAX_SERIES); // 9번째 색을 만들지 않음
});

test('rows keep missing and pending as null, and pick the worst device in a slot', () => {
  const events = [w('u1', 0, 'normal', 0), w('u1', 10, null, null), w('u2', 5, 'caution', 34),
    w('u2', 5, 'danger', 88.2, { device_id: 'laptop', id: 'u2-laptop' })];
  const rows = buildRows(events, ['u1', 'u2']);
  assert.deepEqual(rows.map((r) => r.t), [T0, T0 + 300000, T0 + 600000]);
  assert.deepEqual(rows.map((r) => [r.u1, r.u2]), [[0, null], [null, 88.2], [null, null]]);
  assert.equal(buildMatrix(events).cell('u2', T0 + 300000).id, 'u2-laptop');
  assert.deepEqual(timeTicks(buildMatrix(events).times), [T0, T0 + 300000, T0 + 600000]);
});
