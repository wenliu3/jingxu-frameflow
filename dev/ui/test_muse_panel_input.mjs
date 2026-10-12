import assert from 'node:assert/strict';
import test from 'node:test';
import { createMuseInputQueue } from '../../web/src/musePanel.js';

test('slow Muse input compacts unsent bursts and preserves text, key and click order', async () => {
  const received = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const input = createMuseInputQueue(async action => {
    received.push(action);
    if (received.length === 1) await gate;
  });
  input.enqueue({ kind: 'click', x: 10, y: 20 });
  await Promise.resolve();
  for (const text of 'hello中文') input.enqueue({ kind: 'text', text });
  input.enqueue({ kind: 'key', key: 'Backspace' });
  input.enqueue({ kind: 'text', text: '旁聊' });
  for (let i = 0; i < 12; i++) input.enqueue({ kind: 'wheel', x: 50, y: 60, dx: 0, dy: 75 });
  input.enqueue({ kind: 'click', x: 100, y: 200 });
  release();
  await input.flush();
  assert.deepEqual(received, [
    { kind: 'click', x: 10, y: 20 },
    { kind: 'text', text: 'hello中文' },
    { kind: 'key', key: 'Backspace' },
    { kind: 'text', text: '旁聊' },
    { kind: 'wheel', x: 50, y: 60, dx: 0, dy: 900 },
    { kind: 'click', x: 100, y: 200 },
  ]);
  assert.equal(input.pending, false);
});

test('scroll batching preserves overflow, direction changes and different targets', async () => {
  const received = [];
  const input = createMuseInputQueue(async action => received.push(action));
  for (const dy of [1200, 1200, -300, -400]) input.enqueue({ kind: 'wheel', x: 10, y: 20, dx: 0, dy });
  input.enqueue({ kind: 'wheel', x: 30, y: 20, dx: 0, dy: -100 });
  await input.flush();
  assert.deepEqual(received.map(action => action.dy), [1200, 1200, -700, -100]);
  assert.equal(received.reduce((sum, action) => sum + action.dy, 0), 1600);
});

test('text stays within request limits and closing cancels unsent account inputs', async () => {
  const received = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const input = createMuseInputQueue(async action => {
    received.push(action);
    if (received.length === 1) await gate;
  });
  input.enqueue({ kind: 'text', text: 'a'.repeat(9999) });
  input.enqueue({ kind: 'text', text: 'bc' });
  const flushed = input.flush();
  await Promise.resolve();
  assert.equal(received[0].text.length, 9999);
  input.dispose();
  release();
  await flushed;
  input.enqueue({ kind: 'text', text: 'later' });
  await input.flush();
  assert.equal(received.length, 1);
});

test('failed actions are not replayed and later editing actions retain order', async () => {
  const seen = [], errors = [];
  const input = createMuseInputQueue(async action => {
    seen.push(action.kind);
    if (action.kind === 'click') throw new Error('stale page');
  }, { onError: error => errors.push(error.message) });
  input.enqueue({ kind: 'click', x: 10, y: 20 });
  input.enqueue({ kind: 'key', key: 'Escape' });
  await input.flush();
  assert.deepEqual(seen, ['click', 'key']);
  assert.deepEqual(errors, ['stale page']);
});
