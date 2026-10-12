import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyWorkflow, makeNode, makeShot } from '../../web/src/workflowGraph.js'
import { appendStoryboard, mergeWorkflow, orderedShots, scriptParagraphs, shotReadiness } from '../../web/src/workflowStudio.js'
import { imageCount, imageMode, imageRatio } from '../../web/src/imageModes.js'

test('legacy style selections are discarded on import while personal descriptions and image types remain', () => {
  const source = emptyWorkflow(), target = emptyWorkflow()
  source.nodes.push(makeNode('material', 0, 0, { creationKind: 'image', imageMode: 'scene', materialKind: 'scene', prompt: 'My own description', imageStyle: { id: 'watercolor', label: 'Old style', prompt: 'Old extra style' } }))
  mergeWorkflow(target, source)
  assert.equal(target.nodes[0].data.imageStyle, undefined)
  assert.equal(target.nodes[0].data.prompt, 'My own description')
  assert.equal(target.nodes[0].data.imageMode, 'scene')
})

test('image presets survive backup import while foreign outputs and jobs are cleared', () => {
  for (const mode of ['native', 'character', 'scene', 'prop']) {
    const graph = emptyWorkflow(), source = emptyWorkflow()
    source.nodes.push(makeNode('material', 0, 0, {
      creationKind: 'image', imageMode: mode, materialKind: mode === 'native' ? 'image' : mode,
      prompt: 'Personal style preferences', ratio: '9:16', imageCount: 4, materialName: 'Foreign asset',
      materialFile: 'foreign_sheet.png', status: 'succeeded', jobId: 'foreign',
      mediaVariants: [{ asset_name: 'Foreign', file: 'foreign.png' }],
    }))
    mergeWorkflow(graph, source)
    const data = graph.nodes[0].data
    assert.equal(data.imageMode, mode)
    assert.equal(data.prompt, 'Personal style preferences')
    assert.equal(data.materialKind, 'image')
    assert.equal(data.materialFile, '')
    assert.equal(data.jobId, undefined)
    assert.equal(data.mediaVariants, undefined)
    assert.equal(imageCount(data), 4)
    assert.equal(imageRatio(data), ['character', 'prop'].includes(mode) ? '16:9' : '9:16')
    assert.equal(data.ratio, '9:16', 'sheet modes retain the user ratio for later native/scene use')
  }
  assert.equal(imageMode({}).value, 'native')
  const source = emptyWorkflow()
  source.nodes.push(makeNode('material', 0, 0, { creationKind: 'image', imageMode: 'invalid', materialKind: 'image', prompt: 'x' }))
  assert.throws(() => mergeWorkflow(emptyWorkflow(), source))
  for (const count of [0, 3, '2', true]) {
    source.nodes[0].data.imageMode = 'native'
    source.nodes[0].data.imageCount = count
    assert.throws(() => mergeWorkflow(emptyWorkflow(), source))
  }
  assert.equal(imageCount({}), 1)
  assert.equal(imageCount({ imageMode: 'character' }), 1)
})

test('image and speech imports keep input settings without resuming foreign jobs or media', () => {
  const graph = emptyWorkflow(), document = emptyWorkflow()
  document.nodes.push(makeNode('material', 100, 100, {
    materialKind: 'audio', creationKind: 'audio', prompt: 'A spoken line', voiceId: 'fixture',
    materialName: 'Other project narration', materialFile: 'snd_foreign.mp3',
    materialVersion: 'abcdef', materialIndex: 2, status: 'running', jobId: 'foreign-job',
    mediaSubmission: { request_id: 'a'.repeat(32) },
  }))
  document.nodes.push(makeNode('material', 500, 100, {
    materialKind: 'image', creationKind: 'image', prompt: 'Watercolor road', ratio: '9:16',
    materialName: 'Other project image', materialFile: 'img_foreign.png', status: 'succeeded',
  }))
  assert.equal(mergeWorkflow(graph, document), 2)
  for (const node of graph.nodes) {
    assert.equal(node.data.materialName, '')
    assert.equal(node.data.materialFile, '')
    assert.equal(node.data.status, 'draft')
    for (const field of ['jobId', 'mediaSubmission', 'materialVersion', 'materialIndex'])
      assert.equal(node.data[field], undefined)
  }
  assert.equal(graph.nodes[0].data.voiceId, 'fixture')
  assert.equal(graph.nodes[1].data.ratio, '9:16')
  assert.throws(() => mergeWorkflow(emptyWorkflow(), { ...document, nodes: [{ ...document.nodes[0], data: { ...document.nodes[0].data, prompt: [] } }] }))
})

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
  shot.data.duration = 1
  shot.data.status = 'running'; shot.data.jobId = 'foreign'
  shot.data.versions = [{ id: 'foreign-version', status: 'running', jobId: 'foreign', url: '/files/other/private.mp4' }]
  source.nodes.push(shot, makeNode('video', 400, 0, { status: 'succeeded', url: '/files/other/output.mp4', file: 'output.mp4', jobId: 'foreign' }))
  source.edges.push({ source: source.nodes[0].id, target: source.nodes[1].id })
  const target = emptyWorkflow(); target.revision = 12
  mergeWorkflow(target, source)
  assert.equal(target.revision, 12)
  assert.equal(target.nodes[0].data.status, 'draft')
  assert.equal(target.nodes[0].data.duration, 1)
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
test('imported groups stay together and remain distinct from existing and repeated imports', () => {
  const source = emptyWorkflow(), target = emptyWorkflow()
  source.nodes = ['A', 'B'].map((title, i) => makeNode('note', i * 350, 0, { title, groupId: 'shared', groupTitle: '造型参考' }))
  target.nodes.push(makeNode('note', 0, 0, { groupId: 'shared', groupTitle: '现有组' }))
  mergeWorkflow(target, source)
  mergeWorkflow(target, source)
  assert.equal(target.nodes[1].data.groupId, target.nodes[2].data.groupId)
  assert.equal(target.nodes[3].data.groupId, target.nodes[4].data.groupId)
  assert.equal(target.nodes[1].data.groupTitle, '造型参考')
  assert.equal(new Set(target.nodes.map(n => n.data.groupId)).size, 3)
  const broken = structuredClone(source); broken.nodes[0].data.groupId = {}
  const before = structuredClone(target)
  assert.throws(() => mergeWorkflow(target, broken))
  assert.deepEqual(target, before)
})
