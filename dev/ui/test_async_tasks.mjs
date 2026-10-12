import test from 'node:test';
import assert from 'node:assert/strict';
import { mapConcurrent } from '../../web/src/asyncTasks.js';

test('history reads stay bounded and retain record order despite out-of-order responses', async () => {
  let active = 0;
  let peak = 0;
  const items = Array.from({ length: 24 }, (_, index) => index);
  const results = await mapConcurrent(items, async (item) => {
    peak = Math.max(peak, ++active);
    await new Promise((resolve) => setTimeout(resolve, (5 - item % 6) * 2));
    active--;
    return `record-${item}`;
  });
  assert.equal(peak, 6);
  assert.deepEqual(results, items.map((item) => `record-${item}`));
});

test('leaving the canvas stops scheduling history reads that have not started', async () => {
  let active = true;
  const started = [];
  const release = [];
  const pending = mapConcurrent([0, 1, 2, 3, 4], async (item) => {
    started.push(item);
    await new Promise((resolve) => release.push(resolve));
    return item;
  }, { concurrency: 2, isActive: () => active });
  assert.deepEqual(started, [0, 1]);
  active = false;
  release.forEach((resolve) => resolve());
  assert.deepEqual(await pending, [0, 1, null, null, null]);
  assert.deepEqual(started, [0, 1]);
});

test('empty and inactive imports issue no requests; invalid limits fail instead of hanging', async () => {
  const mapper = () => { throw new Error('unexpected request'); };
  assert.deepEqual(await mapConcurrent([], mapper), []);
  assert.deepEqual(await mapConcurrent([1], mapper, { isActive: () => false }), [null]);
  for (const concurrency of [0, -1, 1.5, Infinity]) {
    await assert.rejects(mapConcurrent([1], mapper, { concurrency }), RangeError);
  }
});

test('mapper failures reject the import and stop queued requests', async () => {
  const started = [];
  await assert.rejects(mapConcurrent([0, 1, 2], async (item) => {
    started.push(item);
    throw new Error('unreadable history');
  }, { concurrency: 1 }), /unreadable history/);
  assert.deepEqual(started, [0]);
});
