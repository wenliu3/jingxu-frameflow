import test from 'node:test'
import assert from 'node:assert/strict'
import { makeShot } from '../../web/src/workflowGraph.js'
import { RESOLUTION_OPTIONS, creationSettings, creationSettingsError, submissionResolution, defaultResolution, videoSelectionError } from '../../web/src/videoSettings.js'

test('new shots inherit defaults while legacy explicit settings remain overrides', () => {
  assert.equal(makeShot(0, 0).data.megapixels, null)
  assert.equal(submissionResolution(null, { video_megapixels: '0.98' }), undefined)
  assert.equal(submissionResolution(0.5, { video_megapixels: '0.98' }), 0.5)
  assert.equal(defaultResolution({ video_megapixels: '0.7' }), 0.7)
  assert.equal(defaultResolution({ video_megapixels: 'broken' }), 0.5)
})
test('API controls do not send unsupported pixel budgets or promise 1080p', () => {
  assert.equal(submissionResolution(0.98, { video_backend: 'api' }), undefined)
  assert.ok(RESOLUTION_OPTIONS.every(o => !/1080|720/.test(o.label)))
})
test('video references respect provider image capabilities', () => {
  const two = [{ kind: 'character' }, { kind: 'scene' }]
  assert.ok(videoSelectionError(two, { video_backend: 'api', video_workflow: 'ref2va' }))
  assert.equal(videoSelectionError(two, { video_workflow: 'ref2va' }), '')
  assert.ok(videoSelectionError([{ kind: 'audio' }], { video_workflow: 'ref2va' }))
  assert.ok(videoSelectionError([{ kind: 'scene' }, { kind: 'scene' }], { video_workflow: 'ref2va' }))
  assert.ok(videoSelectionError(Array.from({ length: 10 }, () => ({ kind: 'image' })), { video_workflow: 'ref2va' }))
})

test('creation controls preserve custom duration and supported candidate settings', () => {
  const data = { duration: 6.5, ratio: '9:16', resolution: '480p', candidateCount: 4, generateAudio: false, exactDuration: true, seed: 0 }
  assert.equal(creationSettingsError(data), '')
  assert.deepEqual(creationSettings(data, {}), { ratio: '9:16', resolution: '480p', candidate_count: 4, generate_audio: false, exact_duration: true, seed: 0 })
  assert.deepEqual(creationSettings(data, { video_backend: 'api' }), { ratio: 'auto', resolution: 'custom', candidate_count: 4, generate_audio: false, exact_duration: false })
  for (const duration of [1, 1.5, 2, 3, 15]) assert.equal(creationSettingsError({ ...data, duration }), '')
  for (const patch of [{ duration: 0.5 }, { duration: 16 }, { seed: -1 }, { candidateCount: 3 }, { ratio: '2:3' }, { resolution: '4k' }]) assert.ok(creationSettingsError({ ...data, ...patch }))
})
