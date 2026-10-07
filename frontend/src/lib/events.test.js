import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeEvent, eventLevel, nullableScore, fetchEventPage, levelFromScore, shouldNotify, formatScore, isNotable, viewStatus, isSummaryItem } from './events.js';

test('company windows show scope, provisional phase and maximum prompt provenance', () => {
  const row = normalizeEvent({ id: 'company-20261004T010000Z', scope: 'company', phase: 'open',
    capture_count: 3, user: 'not-a-personal-grade', prompt_max_score: 50, network_score: 70,
    prompt_source_capture_id: 'b', prompt_source_user_id: 'employee-b', score: 78, grade: 'danger',
    started_at: '2026-10-04T01:00:00Z', ended_at: '2026-10-04T01:05:00Z' });
  assert.equal(row.user, '회사 전체');
  assert.equal(row.dept, '5분 구간 · 3건');
  assert.equal(row.phase, 'open');
  assert.equal(row.prompt_source_capture_id, 'b');
  assert.equal(row.prompt_max_score, 50);
  assert.equal(row.score, 78);
  assert.equal(eventLevel(row), 'danger');
});

test('classification probability is displayed separately from risk score and overall safety', () => {
  const row = normalizeEvent({ id: 'classified', prompt_reasons: [
    { code: 'AI_steal', status: 'complete', detected: true, probability: .9, threshold: .5 },
    { code: 'abuse_act', status: 'complete', detected: false, probability: .5, threshold: .5 },
    { code: 'bad', status: 'error', detected: false, probability: 0, threshold: .5 },
  ] });
  assert.equal(row.prompt_reasons[0].status, 'detected');
  assert.equal(row.prompt_reasons[0].probability, .9);
  assert.equal(row.prompt_reasons[0].score, null);
  assert.equal(row.prompt_reasons[1].status, 'not_detected');
  assert.equal(row.prompt_reasons[2].status, 'error');
  assert.equal(row.prompt_reasons[2].probability, null);
  assert.equal(row.score, null);
  assert.equal(eventLevel(row), 'pending');
});

test('missing values never become safe, zero confidence or fabricated contributions', () => {
  const row = normalizeEvent({ id: 'capture', network_reasons: [{ code: 'N1', status: 'pending' }] });
  assert.equal(eventLevel(row), 'pending');
  assert.equal(row.score, null);
  assert.equal(row.confidence, null);
  assert.equal('weight' in row.network_reasons[0], false);
  assert.equal(row.network_reasons[0].score, null);
});

test('regression classifications use the server cutoff of 0.45', () => {
  const row = normalizeEvent({ id: 'regression', prompt_reasons: [
    { code: 'AI_steal', status: 'complete', detected: true, probability: .46, threshold: .45 },
    { code: 'abuse_act', status: 'complete', detected: false, probability: .45, threshold: .45 },
  ] });
  assert.equal(row.prompt_reasons[0].status, 'detected');
  assert.equal(row.prompt_reasons[1].status, 'not_detected');
  assert.equal(row.prompt_reasons[0].threshold, .45);
  assert.equal(row.prompt_reasons[0].score, null);
  assert.equal(row.score, null);
  assert.equal(eventLevel(row), 'pending');
});

test('zero is a valid completed finding score, not a fused safety verdict', () => {
  const row = normalizeEvent({ id: 'capture', score: 0, prompt_reasons: [{ code: 'x', status: 'complete', score: 0 }] });
  assert.equal(row.prompt_reasons[0].score, 0);
  assert.equal(row.prompt_reasons[0].status, 'complete');
  assert.equal(eventLevel(row), 'pending'); // 서버 등급이 없으면 점수 0이어도 정상으로 보지 않는다
  assert.equal(row.score, 0); // 서버가 보낸 통합 점수 0은 유효한 값
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
    assert.equal(url, '/api/v1/dashboard/company-windows?limit=200');
    return { ok: true, json: async () => ({ events: [{ id: 'real' }], total: 300 }) };
  });
  assert.equal(page.events[0].id, 'real');
  assert.equal(page.total, 300);
  await assert.rejects(fetchEventPage('', undefined, async () => ({ ok: false, status: 503 })), /503/);
  await assert.rejects(fetchEventPage('', undefined, async () => ({ ok: true, json: async () => ({}) })), /응답/);
});

test('4-tier grade: server grade is used for real events and unknown values stay pending', () => {
  assert.equal(eventLevel(normalizeEvent({ id: 'a', grade: 'warning' })), 'warning');
  assert.equal(eventLevel(normalizeEvent({ id: 'b', final_grade: '위험' })), 'danger');
  assert.equal(eventLevel(normalizeEvent({ id: 'c', final_grade: 'unassessed' })), 'pending');
  assert.equal(eventLevel(normalizeEvent({ id: 'd', grade: 'safe' })), 'pending');
  assert.equal(eventLevel(normalizeEvent({ id: 'e', status: 'error', grade: 'normal' })), 'error');
});

test('demo score bands match the backend 30/50/70 boundaries', () => {
  const cases = [[0, 'normal'], [29.9, 'normal'], [30, 'caution'], [49.9, 'caution'], [50, 'warning'], [69.9, 'warning'], [70, 'danger'], [100, 'danger']];
  for (const [score, level] of cases) assert.equal(levelFromScore(score), level);
  assert.equal(levelFromScore(null), 'pending');
});

test('notifications follow server grade changes and ignore pending/error', () => {
  assert.equal(shouldNotify('caution', 'danger', 5, true), true);
  assert.equal(shouldNotify('danger', 'danger', 5, true), false);
  assert.equal(shouldNotify('pending', 'danger', 90, true), false);
  assert.equal(shouldNotify('normal', 'error', 90, true), false);
  assert.equal(shouldNotify('caution', 'warning', 50.5), false); // 데모: 경계선 ±2점 여유
  assert.equal(shouldNotify('caution', 'warning', 53), true);
  assert.equal(shouldNotify('warning', 'caution', 49), false);
  assert.equal(shouldNotify('warning', 'caution', 47), true);
});

test('server score, confidence and override are passed through only when valid', () => {
  const row = normalizeEvent({ id: 'a', grade: 'danger', score: 42.46, confidence: 80, override: true,
    override_reasons: ['프롬프트 점수 52/60 ≥ 50', '', 7] });
  assert.equal(row.score, 42.46);
  assert.equal(row.confidence, 80);
  assert.equal(row.override, true);
  assert.deepEqual(row.overrideReasons, ['프롬프트 점수 52/60 ≥ 50']);
  assert.equal(eventLevel(row), 'danger'); // 점수가 낮아도 서버 등급을 따른다
  const bad = normalizeEvent({ id: 'b', score: '70', confidence: 120, override: 'true' });
  assert.equal(bad.score, null);
  assert.equal(bad.confidence, null);
  assert.equal(bad.override, false);
  assert.equal(eventLevel(normalizeEvent({ id: 'c', score: 95 })), 'pending'); // 등급 없이 점수만으로 등급을 만들지 않는다
});

test('formatScore keeps integers and trims decimals', () => {
  assert.equal(formatScore(63), '63');
  assert.equal(formatScore(63.456), '63.5');
  assert.equal(formatScore(0), '0');
  assert.equal(formatScore(null), '—');
});

test('pagination reads 200-row pages by offset, merges, de-duplicates and caps pages', async () => {
  const urls = [];
  const rows = { '': [{ id: '1' }, { id: '2' }], '&offset=200': [{ id: '2' }, { id: '3' }], '&offset=400': [{ id: '4' }] };
  const page = await fetchEventPage('http://x', undefined, async (url) => {
    urls.push(url);
    const key = url.includes('&offset=') ? url.slice(url.indexOf('&offset=')) : '';
    return { ok: true, json: async () => ({ events: rows[key] ?? [], total: 450 }) };
  }, 3);
  assert.deepEqual(urls, [
    'http://x/api/v1/dashboard/company-windows?limit=200',
    'http://x/api/v1/dashboard/company-windows?limit=200&offset=200',
    'http://x/api/v1/dashboard/company-windows?limit=200&offset=400',
  ]);
  assert.deepEqual(page.events.map((e) => e.id), ['1', '2', '3', '4']);
  assert.equal(page.total, 450);
  const capped = [];
  await fetchEventPage('', undefined, async (url) => { capped.push(url); return { ok: true, json: async () => ({ events: [], total: 0 }) }; }, 99);
  assert.equal(capped.length, 10);
  await assert.rejects(fetchEventPage('', undefined, async (url) =>
    url.includes('offset=200') ? { ok: false, status: 500 } : { ok: true, json: async () => ({ events: [], total: 0 }) }, 2), /500/);
});

test('notable items use server detections instead of probability or reason heuristics', () => {
  const row = normalizeEvent({ id: 'n', network_reasons: [
    { code: 'N1', status: 'complete', detected: true, probability: .4, threshold: null, detection_method: 'argmax', score: 40, detail: '모델 확률 98.4% · 판정 위협' },
    { code: 'N2', status: 'complete', detected: false, probability: 0, threshold: null, detection_method: 'argmax', score: 0, detail: '모델 확률 0.0%' },
    { code: 'network_score', status: 'complete', score: 69.7, detail: '기본 50 + 모델 확신도' },
    { code: 'network_model', status: 'pending', score: null, detail: '미판정' },
    { code: 'N3', status: 'complete', score: 12, detail: '모델 확률 12.0% · 판정 위험' },
    { code: 'N4', status: 'error', score: null, detail: '' },
  ], prompt_reasons: [
    { code: 'AI_steal', status: 'complete', detected: true, probability: .9, threshold: .5 },
    { code: 'abuse_act', status: 'complete', detected: false, probability: .12, threshold: .5 },
  ] });
  const hits = [...row.network_reasons, ...row.prompt_reasons].filter(isNotable).map((i) => i.code);
  assert.deepEqual(hits, ['N1', 'AI_steal']); // network_score는 요약 행이라 탐지에서 제외
  assert.equal(isSummaryItem(row.network_reasons[2]), true);
  assert.equal(isSummaryItem(row.network_reasons[3]), true);
  assert.equal(viewStatus(row.network_reasons[0]), 'detected');
  assert.equal(viewStatus(row.network_reasons[1]), 'not_detected');
  assert.equal(viewStatus(row.network_reasons[2]), 'complete'); // 요약 행은 '탐지'로 표시하지 않음
  assert.equal(viewStatus(row.prompt_reasons[1]), 'not_detected');
  assert.equal(isNotable(row.network_reasons[3]), false); // 미판정은 탐지가 아님
  assert.equal(isNotable(row.network_reasons[5]), false); // 오류는 별도 표시
});
