import test from 'node:test'
import assert from 'node:assert/strict'
import { applyTheme, readTheme, THEME_KEY } from '../../web/src/theme.js'

test('theme defaults to night and only accepts a saved day preference', () => {
  for (const value of [null, 'dark', 'invalid']) assert.equal(readTheme({ getItem: () => value }), 'dark')
  assert.equal(readTheme({ getItem: key => key === THEME_KEY ? 'light' : null }), 'light')
  assert.equal(readTheme({ getItem: () => { throw new Error('Storage blocked') } }), 'dark')
})

test('switching updates native controls and remains usable when storage is blocked', () => {
  const attributes = {}, saved = {}
  const previousDocument = globalThis.document, previousWindow = globalThis.window
  try {
    globalThis.document = { documentElement: { dataset: {}, style: {} }, querySelector: () => ({ setAttribute: (key, value) => { attributes[key] = value } }) }
    globalThis.window = { localStorage: { setItem: (key, value) => { saved[key] = value } } }
    assert.equal(applyTheme('light'), 'light')
    assert.equal(document.documentElement.dataset.theme, 'light')
    assert.equal(document.documentElement.style.colorScheme, 'light')
    assert.equal(saved[THEME_KEY], 'light')
    assert.equal(attributes.content, '#f5f5f5')
    window.localStorage.setItem = () => { throw new Error('Storage blocked') }
    assert.equal(applyTheme('invalid'), 'dark')
    assert.equal(document.documentElement.dataset.theme, 'dark')
  } finally {
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})
