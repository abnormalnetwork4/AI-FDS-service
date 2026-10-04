import test from 'node:test';
import assert from 'node:assert/strict';
import { watchEvents } from './live.js';

class Source {
  static latest;
  constructor(url) { this.url = url; Source.latest = this; }
  addEventListener(name, callback) { this[name] = callback; }
  close() { this.closed = true; }
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('change during an in-flight fetch is not lost, reconnect reloads and cleanup closes stream', async () => {
  let release, calls = 0;
  const pages = [], modes = [];
  const stop = watchEvents({base:'', Source, onMode: (m) => modes.push(m),
    onError: assert.fail, onData: (data) => pages.push(data),
    fetchPage: async () => { calls++; if(calls === 1) await new Promise((r) => { release = r; }); return calls; }});
  try {
    assert.equal(Source.latest.url, '/api/v1/dashboard/stream');
    Source.latest.onopen();
    Source.latest.changed();
    release();
    await tick();
    assert.deepEqual(pages, [1, 2]);
    assert.equal(modes.at(-1), 'stream');
    Source.latest.onerror();
    await tick();
    assert.equal(modes.at(-1), 'polling');
    Source.latest.onopen();
    await tick();
    assert.equal(pages.at(-1), 4);
  } finally { stop(); }
  assert.equal(Source.latest.closed, true);
});

test('unmount aborts requests and suppresses late updates', async () => {
  let release, signal, updates = 0;
  const stop = watchEvents({base:'', Source, onMode: () => {}, onError: assert.fail,
    onData: () => updates++, fetchPage: async (s) => {
      signal = s; await new Promise((r) => { release = r; }); return {};
    }});
  stop(); release(); await tick();
  assert.equal(signal.aborted, true);
  assert.equal(updates, 0);
});

test('failed list fetch retries even when SSE stays connected without further changes', async () => {
  let calls = 0, failures = 0, recovered;
  const done = new Promise((resolve) => { recovered = resolve; });
  const stop = watchEvents({ base:'', Source, pollMs:10, onMode: () => {},
    onError: () => failures++, onData: recovered,
    fetchPage: async () => { if (++calls <= 2) throw new Error('temporary'); return 'recovered'; } });
  try {
    Source.latest.onopen();
    assert.equal(await done, 'recovered');
    assert.equal(failures, 2);
  } finally { stop(); }
});
