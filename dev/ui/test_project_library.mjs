import test from 'node:test'
import assert from 'node:assert/strict'
import { projectState, matchesProject } from '../../web/src/projectLibrary.js'

test('successful draft does not claim a generated video', () => {
  assert.deepEqual(projectState({ status: 'succeeded', stage_state: 'draft' }), { key: 'draft', label: '草稿' })
  assert.equal(projectState({ shots: 7 }).label, '分镜编排')
  assert.equal(projectState({ material_count: 28 }).label, '素材准备')
})
test('active generation and failures remain visible even with existing videos', () => {
  assert.equal(projectState({ status: 'running', video_count: 6 }).key, 'running')
  assert.equal(projectState({ status: 'failed', video_count: 6 }).key, 'failed')
  assert.equal(projectState({ status: 'succeeded', video_count: 6 }).key, 'succeeded')
})
test('search and filters use visible project state rather than task success', () => {
  const draft = { title: '我的故事 Frameflow', status: 'succeeded' }
  assert.equal(matchesProject(draft, ' frameFLOW ', 'draft'), true)
  assert.equal(matchesProject(draft, '', 'succeeded'), false)
  assert.equal(matchesProject({ ...draft, video_count: 1 }, '故事', 'succeeded'), true)
  assert.equal(matchesProject(draft, '别的故事', 'all'), false)
})
