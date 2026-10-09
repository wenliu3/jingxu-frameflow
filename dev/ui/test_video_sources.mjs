import test from 'node:test'
import assert from 'node:assert/strict'
import { canConnect, makeNode, makeShot } from '../../web/src/workflowGraph.js'
import { sourceContext, sourceSnapshot, sourceVersion, videoInputs, videoSourcesError } from '../../web/src/videoSources.js'
import { mergeWorkflow, orderedShots } from '../../web/src/workflowStudio.js'

const cfg = { video_workflow: 'ref2va', video_backend: 'comfyui' }
test('uploaded footage connects as a visual source and stays outside generation and export order', () => {
  const origin = makeNode('footage', 0, 0, { title: 'Upload', versions: [{ id: 'uploaded', file: 'upload_012345abcdef.mp4', url: '/uploaded.mp4', status: 'succeeded' }] })
  const target = makeShot(365, 0)
  const graph = { nodes: [origin, target], edges: [{ id: 'link', source: origin.id, target: target.id, usage: 'reference' }] }
  assert.equal(canConnect(graph.nodes, [], origin.id, target.id), '')
  assert.ok(canConnect(graph.nodes, [], target.id, origin.id))
  assert.deepEqual(orderedShots(graph), [target])
  const inputs = videoInputs(graph, target.id)
  assert.equal(videoSourcesError(inputs, cfg), '')
  assert.equal(sourceSnapshot(inputs)[0].file, 'upload_012345abcdef.mp4')
  const imported = { nodes: [], edges: [] }
  mergeWorkflow(imported, { ...graph, version: 1 })
  assert.equal(imported.nodes[0].data.versions.length, 0)
  assert.match(videoSourcesError(videoInputs(imported, imported.nodes[1].id), cfg, imported.nodes.map(n => n.id)), /重新上传/)
})
function fixture(usage = 'continue') {
  const origin = makeShot(0, 0), target = makeShot(365, 0)
  origin.data.description = 'changed draft'
  origin.data.versions = [{ id: 'old', status: 'succeeded', file: 'old.mp4', url: '/old.mp4', description: 'original action' }, { id: 'new', status: 'succeeded', file: 'new.mp4', url: '/new.mp4', description: 'new action' }]
  origin.data.activeVersionId = 'new'
  return { nodes: [target, origin], edges: [{ id: 'link', source: origin.id, target: target.id, usage, sourceVersionId: 'old' }] }
}
test('explicit version pins both actual media and original description', () => {
  const graph = fixture(), inputs = videoInputs(graph, graph.nodes[0].id)
  assert.equal(sourceVersion(inputs[0].node, inputs[0].edge).id, 'old')
  const snapshot = sourceSnapshot(inputs)
  assert.equal(snapshot[0].file, 'old.mp4')
  assert.match(sourceContext(inputs), /original action/)
  graph.nodes[1].data.activeVersionId = 'new'
  inputs[0].edge.sourceVersionId = 'new'
  assert.equal(snapshot[0].file, 'old.mp4')
  assert.equal(sourceSnapshot(inputs)[0].file, 'new.mp4')
})
test('old unmarked links stay text and never submit media', () => {
  const graph = fixture(undefined)
  delete graph.edges[0].usage
  const inputs = videoInputs(graph, graph.nodes[0].id)
  assert.deepEqual(sourceSnapshot(inputs), [])
  assert.match(sourceContext(inputs), /仅文字前情/)
  assert.equal(videoSourcesError(inputs, { video_workflow: 'i2v' }), '')
})
test('pending dependencies wait and may join a correctly ordered batch', () => {
  const graph = fixture(), origin = graph.nodes[1]
  graph.edges[0].sourceVersionId = ''
  origin.data.versions = []
  const inputs = videoInputs(graph, graph.nodes[0].id)
  assert.match(videoSourcesError(inputs, cfg), /等待/)
  assert.equal(videoSourcesError(inputs, cfg, [origin.id]), '')
  assert.equal(orderedShots(graph)[0].id, origin.id)
  assert.throws(() => sourceSnapshot(inputs), /等待/)
  graph.edges[0].sourceVersionId = 'missing'
  assert.match(videoSourcesError(inputs, cfg, [origin.id]), /等待/)
})
test('reject conflicting origins, excessive references and invalid range', () => {
  const graph = fixture(), inputs = videoInputs(graph, graph.nodes[0].id)
  assert.match(videoSourcesError(inputs, { video_workflow: 'i2v' }), /Ref2VA/)
  assert.match(videoSourcesError([...inputs, ...inputs], cfg), /一个续拍起点/)
  assert.match(videoSourcesError([...inputs, ...inputs, ...inputs, ...inputs], cfg), /三个/)
  inputs[0].edge.start = 3; inputs[0].edge.end = 2
  assert.match(videoSourcesError(inputs, cfg), /时间范围/)
})
test('imports retain visual purpose but clear foreign media and version bindings', () => {
  const graph = fixture(), target = { nodes: [], edges: [] }
  mergeWorkflow(target, { ...graph, version: 1 })
  assert.equal(target.edges[0].usage, 'continue')
  assert.equal(target.edges[0].sourceVersionId, '')
  assert.ok(target.nodes.every(n => n.data.versions.length === 0))
  assert.match(videoSourcesError(videoInputs(target, target.edges[0].target), cfg), /等待/)
})
