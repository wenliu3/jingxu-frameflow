import test from 'node:test'
import assert from 'node:assert/strict'
import { settingsPatch, workflowPreset, loraPreset, settingsError, LORAS } from '../../web/src/serviceSettings.js'
import { api } from '../../web/src/api.js'

test('draft patches preserve unrelated settings and allow explicit clearing', () => {
  const baseline = { text_api_key: 'saved', video_steps: '4', video_lora: 'lora' }
  assert.deepEqual(settingsPatch(baseline, { ...baseline, video_steps: 4, video_lora: '' }), { video_lora: '' })
  assert.deepEqual(settingsPatch(baseline, baseline), {})
})

test('workflow and acceleration presets always select a compatible pair', () => {
  for (const workflow of ['i2v', 'ref2va']) {
    const preset = workflowPreset(workflow)
    const lora = LORAS.find(item => item.value === preset.video_lora)
    assert.equal(lora.workflow, workflow)
    assert.equal(preset.video_steps, lora.steps)
  }
  assert.deepEqual(loraPreset(''), { video_lora: '', video_steps: '20' })
  assert.equal(loraPreset(LORAS[3].value).video_workflow, 'ref2va')
})

test('invalid sampling or incompatible workflows are caught before saving', () => {
  const valid = { video_backend: 'comfyui', ...workflowPreset('i2v'), video_megapixels: '0.5', video_timeout_s: '3600' }
  assert.equal(settingsError(valid), '')
  assert.ok(settingsError({ ...valid, video_steps: 1.5 }))
  assert.ok(settingsError({ ...valid, video_megapixels: 'NaN' }))
  assert.ok(settingsError({ ...valid, video_workflow: 'ref2va' }))
})

test('concurrent configuration reads share a request but later reads refresh', async () => {
  const original = globalThis.fetch
  let finish, calls = 0
  globalThis.fetch = async () => {
    calls++
    if (calls === 1) await new Promise(resolve => { finish = resolve })
    return { ok: true, json: async () => ({ model: 'test' }) }
  }
  try {
    const first = api.getConfig(), second = api.getConfig()
    assert.equal(calls, 1)
    finish()
    await Promise.all([first, second])
    await api.getConfig()
    assert.equal(calls, 2)
  } finally { globalThis.fetch = original }
})

test('failed reads are removed from the shared request pool', async () => {
  const original = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => ++calls === 1
    ? { ok: false, status: 500, text: async () => 'test failure' }
    : { ok: true, json: async () => ({}) }
  try {
    await assert.rejects(api.getConfig(), /test failure/)
    await api.getConfig()
    assert.equal(calls, 2)
  } finally { globalThis.fetch = original }
})

test('settings use the patch endpoint and connection testing sends only the draft address', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return { ok: true, json: async () => ({}) } }
  try {
    await api.setConfig({ video_steps: '8' })
    await api.testConnection('http://localhost:8188')
    assert.equal(calls[0].method, 'PATCH')
    assert.deepEqual(JSON.parse(calls[0].body), { video_steps: '8' })
    assert.deepEqual(JSON.parse(calls[1].body), { comfyui_url: 'http://localhost:8188' })
  } finally { globalThis.fetch = original }
})
