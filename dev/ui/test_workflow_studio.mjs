import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js'
import { appendStoryboard, mergeWorkflow, orderedShots, scriptParagraphs, shotReadiness } from '../../web/src/workflowStudio.js'

test('story order follows connections even after nodes have been repositioned', () => {
  const graph = { nodes: [{ id: 'end', type: 'shot' }, { id: 'start', type: 'shot' }, { id: 'middle', type: 'shot' }], edges: [{ source: 'start', target: 'middle' }, { source: 'middle', target: 'end' }] }
  assert.deepEqual(orderedShots(graph).map(n => n.id), ['start', 'middle', 'end'])
})
test('paragraphs preserve multiline dialogue and only split at blank lines', () => {
  assert.deepEqual(scriptParagraphs('  雨夜\n人物对白\n\n\n晨光  '), ['雨夜\n人物对白', '晨光'])
})
test('building a storyboard creates one creation card per shot with no separate output nodes', () => {
  const graph = emptyWorkflow(), material = makeNode('material', 70, 60, {})
  graph.nodes.push(material)
  const shots = appendStoryboard(graph, ['镜头一', '镜头二'], { duration: 5, sources: [material.id] })
  assert.deepEqual(orderedShots(graph), shots)
  assert.equal(graph.nodes.length, 3)
  assert.equal(graph.nodes.filter(n => n.type === 'video').length, 0)
  assert.equal(graph.edges.filter(e => e.source === material.id).length, 2)
  assert.ok(graph.edges.some(e => e.source === shots[0].id && e.target === shots[1].id))
  assert.ok(shots.every(s => s.data.duration === 5))
})
test('readiness checks actual provider constraints before submitting work', () => {
  const shot = makeShot(0, 0), cfg = { comfyui_url: 'http://local', video_workflow: 'i2v', text_api_key: 'test' }
  shot.data.description = 'rain'
  assert.ok(shotReadiness(shot, [], cfg))
  assert.ok(shotReadiness(shot, [null], cfg))
  assert.ok(shotReadiness(shot, [{ kind: 'image', ready: true }, { kind: 'scene', ready: true }], cfg))
  assert.equal(shotReadiness(shot, [{ kind: 'prop', ready: true }], cfg), '')
  assert.equal(shotReadiness(shot, [{ kind: 'image', ready: true }, { kind: 'scene', ready: true }], { ...cfg, video_workflow: 'ref2va' }), '')
  shot.data.manual = true
  assert.equal(shotReadiness(shot, [{ kind: 'image', ready: true }], { ...cfg, text_api_key: '' }), '')
})
test('imports keep project revision and never resume foreign generation or video URLs', () => {
  const source = emptyWorkflow(), shot = makeShot(30, -100)
  shot.data.status = 'running'; shot.data.jobId = 'foreign'
  shot.data.versions = [{ id: 'foreign-version', status: 'running', jobId: 'foreign', url: '/files/other/private.mp4' }]
  source.nodes.push(shot, makeNode('video', 400, 0, { status: 'succeeded', url: '/files/other/output.mp4', file: 'output.mp4', jobId: 'foreign' }))
  source.edges.push({ source: source.nodes[0].id, target: source.nodes[1].id })
  const target = emptyWorkflow(); target.revision = 12
  mergeWorkflow(target, source)
  assert.equal(target.revision, 12)
  assert.equal(target.nodes[0].data.status, 'draft')
  assert.equal(target.nodes.length, 1)
  assert.deepEqual(target.nodes[0].data.versions, [])
  assert.ok(target.nodes.every(n => !n.data.url && !n.data.jobId))
  assert.notEqual(target.nodes[0].id, shot.id)
})
test('invalid and oversized imports leave the existing canvas intact', () => {
  const target = emptyWorkflow(), source = emptyWorkflow()
  const first = makeShot(0, 0), second = makeShot(300, 0)
  source.nodes = [first, second]; source.edges = [{ source: first.id, target: second.id }, { source: second.id, target: first.id }]
  assert.throws(() => mergeWorkflow(target, source))
  assert.deepEqual(target.nodes, [])
  assert.throws(() => appendStoryboard(target, Array(31).fill('镜头')))
  assert.throws(() => appendStoryboard(target, ['x'.repeat(1001)]))
  assert.deepEqual(target.nodes, [])
})
