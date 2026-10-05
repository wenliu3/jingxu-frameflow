import test from 'node:test'
import assert from 'node:assert/strict'
import { canConnect, arrange, graphBounds, seedWorkflow } from '../../web/src/workflowGraph.js'

const nodes = [
  { id: 'material', type: 'material', x: 0, y: 0 },
  { id: 'first', type: 'shot', x: 0, y: 0 },
  { id: 'second', type: 'shot', x: 0, y: 0 },
  { id: 'video', type: 'video', x: 0, y: 0 },
]
const edges = [
  { source: 'material', target: 'first' },
  { source: 'first', target: 'second' },
  { source: 'second', target: 'video' },
]

test('branching can reuse a material without introducing cycles', () => {
  assert.equal(canConnect(nodes, edges, 'material', 'second'), '')
  assert.ok(canConnect(nodes, edges, 'second', 'first'))
  assert.ok(canConnect(nodes, edges, 'first', 'first'))
  assert.ok(canConnect(nodes, edges, 'first', 'second'))
  assert.ok(canConnect(nodes, edges, 'video', 'material'))
})

test('automatic arrangement respects dependencies and does not mutate the graph', () => {
  const positioned = arrange(nodes, edges)
  for (const edge of edges) {
    assert.ok(positioned.find(n => n.id === edge.target).x > positioned.find(n => n.id === edge.source).x)
  }
  assert.equal(nodes[0].x, 0)
  const bounds = graphBounds(positioned)
  assert.ok(bounds.width > 1000)
  assert.ok(bounds.height > 250)
})

test('starter canvas distinguishes reference previews from real historical videos', () => {
  const graph = seedWorkflow([
    { kind: 'scene', name: '场景', file: 'scene.png' },
    { kind: 'character', name: '主角', file: 'actor.png' },
  ], [{ name: 'old.mp4', url: '/files/task/segments/old.mp4' }])
  const video = graph.nodes.find(n => n.data.versions?.some(v => v.file === 'old.mp4'))
  assert.equal(video.data.status, 'succeeded')
  assert.equal(graph.nodes.filter(n => n.type === 'shot')[0].data.description, '')
  assert.equal(graph.nodes.filter(n => n.type === 'material').length, 2)
  assert.equal(graph.nodes.filter(n => n.type === 'shot').length, 2)
  assert.equal(graph.nodes.filter(n => n.type === 'video').length, 0)
  assert.equal(graph.nodes.filter(n => n.type === 'shot')[0].data.versions.length, 0)
})

test('a project without assets is still editable', () => {
  const graph = seedWorkflow([])
  assert.equal(graph.nodes.length, 1)
  assert.equal(graph.nodes[0].type, 'shot')
  assert.equal(graph.edges.length, 0)
})
