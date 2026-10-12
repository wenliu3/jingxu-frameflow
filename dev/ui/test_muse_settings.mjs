import test from 'node:test';
import assert from 'node:assert/strict';
import { creationSettings, submissionResolution, videoSelectionError } from '../../web/src/videoSettings.js';
import { shotReadiness } from '../../web/src/workflowStudio.js';
import { videoSourcesError, videoMentionMaterials } from '../../web/src/videoSources.js';

test('Muse forwards desired aspect and resolution, skips precision controls and submits one candidate', () => {
  const cfg = { video_backend: 'muse', video_workflow: 'ref2va' };
  const data = { duration: 10, ratio: '16:9', resolution: '720p', seed: 42, candidateCount: 4 };
  assert.deepEqual(creationSettings(data, cfg), { ratio: '16:9', resolution: '720p', candidate_count: 1, generate_audio: true, exact_duration: false });
  assert.equal(submissionResolution(0.9, cfg), undefined);
  assert.equal(videoSelectionError([], cfg), '');
  assert.equal(shotReadiness({ data: { ...data, description: '竹林里的熊猫' } }, [], cfg), '');
  const inputs = [{ node: { id: 'source', data: { title: '来源', versions: [{ id: 'v1', status: 'succeeded', file: 'seg_123456abcdef.mp4', url: '/files/clip.mp4' }] } }, edge: { usage: 'reference' } }];
  assert.equal(videoSourcesError(inputs, cfg), '');
  assert.equal(videoMentionMaterials(inputs)[0].ready, true);
  assert.match(videoSourcesError(inputs, { video_backend: 'api' }), /ComfyUI/);
});

test('Muse rejects audio and too many photos without silently dropping inputs', () => {
  const cfg = { video_backend: 'muse' };
  assert.match(videoSelectionError([{ kind: 'audio' }], cfg), /音频/);
  assert.equal(videoSelectionError([{ kind: 'character' }, { kind: 'scene' }], cfg), '');
  assert.match(videoSelectionError(Array(10).fill({ kind: 'image' }), cfg), /9/);
});
