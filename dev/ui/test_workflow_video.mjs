import test from 'node:test'
import assert from 'node:assert/strict'
import { clone, emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js'
import { applyVideoProgress, currentVideo, recordSettings, unifyVideoNodes, videoVersions } from '../../web/src/workflowVideo.js'

test('legacy shot and all output cards merge without losing references, versions or pending jobs', () => {
  const graph = emptyWorkflow(), shot = makeShot(400, 60), material = makeNode('material', 0, 0)
  shot.data.title = '我的开场'; shot.data.description = '晨光'; shot.data.status = 'running'; shot.data.jobId = 'current'
  const first = makeNode('video', 800, 60, { status: 'succeeded', file: 'first.mp4', url: '/files/project/first.mp4' })
  const second = makeNode('video', 800, 350, { status: 'succeeded', file: 'second.mp4', url: '/files/project/second.mp4' })
  const pending = makeNode('video', 800, 650, { status: 'running', jobId: 'current' })
  const empty = makeNode('video', 800, 950, { status: 'empty' })
  graph.nodes = [material, shot, first, second, pending, empty]
  graph.edges = [{ source: material.id, target: shot.id }, ...[first, second, pending, empty].map(n => ({ source: shot.id, target: n.id }))]
  unifyVideoNodes(graph)
  assert.equal(graph.nodes.length, 2)
  assert.equal(graph.edges.length, 1)
  assert.equal(shot.data.description, '晨光')
  assert.equal(shot.data.title, '我的开场')
  assert.equal(videoVersions(shot).length, 3)
  assert.equal(videoVersions(shot).at(-1).jobId, 'current')
  assert.equal(currentVideo(shot).file, 'second.mp4', 'old output remains visible while regenerating')
  shot.data.activeVersionId = first.id
  assert.equal(currentVideo(shot).file, 'first.mp4')
  const snapshot = clone(graph)
  unifyVideoNodes(graph)
  assert.deepEqual(graph, snapshot, 'migration is idempotent')
})

test('standalone historical videos become playable, editable creation cards', () => {
  const graph = emptyWorkflow(), original = makeNode('video', 100, 200, { title: '历史视频', status: 'succeeded', file: 'old.mp4', url: '/files/project/old.mp4' })
  graph.nodes = [original]
  unifyVideoNodes(graph)
  assert.equal(original.type, 'shot')
  assert.equal(original.id, graph.nodes[0].id)
  assert.equal(original.x, 100)
  assert.equal(currentVideo(original).file, 'old.mp4')
  assert.equal(original.data.description, '')
})

test('failed attempts do not replace the selected successful version', () => {
  const node = makeShot(0, 0)
  node.data.versions = [{ id: 'done', status: 'succeeded', url: '/files/project/done.mp4' }, { id: 'failed', status: 'failed', error: 'error' }]
  node.data.activeVersionId = 'failed'
  assert.equal(currentVideo(node).id, 'done')
})

test('history restores custom timing and actual candidate settings instead of rounding to old presets', () => {
  const settings = recordSettings({ note: '晨光', duration: 6.5, ratio: '9:16', resolution: '480p', candidate_count: 4, generate_audio: false, exact_duration: true, seed: 0, megapixels: .41 })
  assert.deepEqual(settings, { description: '晨光', manual: false, duration: 6.5, ratio: '9:16', resolution: '480p', candidateCount: 4, generateAudio: false, exactDuration: true, seed: 0, megapixels: .41 })
  assert.equal(recordSettings({ duration: 6.5, planned_duration: 7.25 }).duration, 7.25)
  assert.equal(recordSettings({ duration: 'broken' }).duration, 5)
  assert.equal(recordSettings({ video_backend: 'api', megapixels: .5 }).megapixels, null)
  assert.equal(recordSettings({}).resolution, 'custom', 'old records keep their pixel-budget behavior')
})

test('incremental candidates retain successful output and never overwrite a chosen version', () => {
  const node = makeShot(0, 0)
  node.data.versions = [{ id: 'old', status: 'succeeded', url: '/files/project/old.mp4' }, ...[0, 1, 2].map(index => ({ id: `v${index}`, jobId: 'batch', candidateIndex: index, status: 'running' }))]
  const job = { job_id: 'batch', done: 1, total: 3, results: [{ index: 0, status: 'succeeded', video_url: '/files/project/first.mp4', seed: 0 }, { index: 1, status: 'running' }, { index: 2, status: 'queued' }] }
  applyVideoProgress(node, job, 'project')
  assert.equal(currentVideo(node).id, 'v0')
  node.data.activeVersionId = 'old'
  job.results[1] = { index: 1, status: 'succeeded', video_url: '/files/project/second.mp4' }
  job.results[2] = { index: 2, status: 'failed', error: 'failed' }
  applyVideoProgress(node, job, 'project')
  assert.equal(currentVideo(node).id, 'old')
  assert.equal(node.data.versions[3].status, 'failed')
  assert.equal(node.data.versions[1].seed, 0)
  assert.equal(node.data.versions[2].file, 'second.mp4')
  job.results[1].video_url = '/files/another-project/secret.mp4'
  applyVideoProgress(node, job, 'project')
  assert.equal(node.data.versions[2].file, 'second.mp4')
})
