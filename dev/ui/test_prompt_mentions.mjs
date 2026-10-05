import test from 'node:test'
import assert from 'node:assert/strict'
import { mentionToken, promptParts, displayPrompt, mentionsError, mentionQuery, numberMaterials, connectedMentionChoices } from '../../web/src/promptMentions.js'

test('material identity survives names with punctuation, newlines, duplicate kinds and Unicode', () => {
  const name = '云糯糯 {侧面}: 50% / @英雄\n第二行'
  const token = mentionToken({ kind: 'character', name })
  assert.deepEqual(promptParts(`开场${token}走来`), [{ text: '开场' }, { token, kind: 'character', name }, { text: '走来' }])
  assert.equal(displayPrompt(token), `@${name}`)
  assert.equal(mentionsError(token, [{ kind: 'prop', name, ready: true }]).includes(name), true)
  assert.equal(mentionsError(token, [{ kind: 'character', name, ready: true }]), '')
})
test('disconnects and missing references do not silently redirect a prompt', () => {
  assert.ok(mentionsError('@{image:First}', []))
  assert.ok(mentionsError('@{image:First}', [{ kind: 'image', name: 'First', ready: false }]))
  assert.equal(displayPrompt('普通文本 <Picture 1> @{image:%ZZ}'), '普通文本 <Picture 1> @{image:%ZZ}')
})
test('mention search respects caret and does not trigger in existing tokens or email addresses', () => {
  assert.deepEqual(mentionQuery('在 @山海 晨光中', 5), { start: 2, end: 5, query: '山海' })
  assert.equal(mentionQuery('@{image:First}', 14), null)
  assert.equal(mentionQuery('test@example.com', 16), null)
  assert.deepEqual(mentionQuery('人物@', 3), { start: 2, end: 3, query: '' })
})
test('numbered references match backend Picture order and @ never offers unrelated assets', () => {
  const connected = numberMaterials([{ kind: 'scene', name: '场景' }, { kind: 'image', name: '图片' }, { kind: 'character', name: '角色乙' }, { kind: 'prop', name: '道具' }, { kind: 'character', name: '角色甲' }])
  assert.deepEqual(connected.map(m => [m.name, m.number]), [['角色乙', 1], ['角色甲', 2], ['道具', 3], ['场景', 4], ['图片', 5]])
  assert.deepEqual(connectedMentionChoices(connected, '角色').map(m => m.number), [1, 2])
  assert.deepEqual(connectedMentionChoices(connected, '未连接素材'), [])
})
