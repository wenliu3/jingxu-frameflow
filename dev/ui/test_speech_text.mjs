import test from 'node:test';
import assert from 'node:assert/strict';
import { speechParts } from '../../web/src/speechText.js';
import { audioPeaks, audioTime } from '../../web/src/audioWaveform.js';

test('speech tags keep the exact original text and unknown pronunciation hints', () => {
  const text = '你好(chuckle)<#0.25#>世界\n这是(he2)平。(clear-throat)<#99.99#>结束';
  const parts = speechParts(text);
  assert.equal(parts.map(part => part.raw).join(''), text);
  assert.equal(parts.find(part => part.raw === '(chuckle)').label, '(轻笑)');
  assert.equal(parts.filter(part => part.type === 'pause').length, 2);
  assert.ok(parts.some(part => part.type === 'text' && part.raw.includes('(he2)')));
  assert.ok(speechParts('<#0#><#100#><#0.001#>(unknown)').every(part => part.type === 'text'));
});
test('waveforms use actual samples from all channels and preserve silence', () => {
  const data = [new Float32Array([0, .2, 0, 0, .4, 0, 0, 0]), new Float32Array([0, 0, .8, 0, 0, 0, 0, 0])];
  assert.deepEqual(audioPeaks({ numberOfChannels: 2, getChannelData: channel => data[channel] }, 4), [.25, 1, .5, 0]);
  assert.deepEqual(audioPeaks({ numberOfChannels: 1, getChannelData: () => new Float32Array(8) }, 4), [0, 0, 0, 0]);
  assert.equal(audioTime(125.5), '02:05');
});
