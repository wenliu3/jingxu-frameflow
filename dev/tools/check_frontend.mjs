// Check source reachability and component imports without starting a model service.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const sourceRoot = path.join(root, 'web/src')
const require = createRequire(path.join(root, 'web/package.json'))
const { parse: parseVue, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const { parse: parseJs } = require('@babel/parser')
const problems = [], graph = new Map(), apiCalls = [], apiMethods = new Set()

function walk(node, visit) {
  if (!node || typeof node !== 'object') return
  visit(node)
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'comments', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue
    if (Array.isArray(value)) value.forEach(child => walk(child, visit))
    else if (value && typeof value === 'object') walk(value, visit)
  }
}

function files(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const filename = path.join(directory, entry.name)
    return entry.isDirectory() ? files(filename) : /\.(vue|js|css)$/.test(filename) ? [filename] : []
  })
}

function resolve(from, reference) {
  if (!reference.startsWith('.')) return null
  const target = path.resolve(path.dirname(from), reference)
  const found = [target, ...['.js', '.vue', '.css', '/index.js'].map(ext => target + ext)]
    .find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isFile())
  if (!found) problems.push(`${path.relative(root, from)}: missing ${reference}`)
  return found
}

for (const filename of files(sourceRoot)) {
  const text = fs.readFileSync(filename, 'utf8'), dependencies = []
  graph.set(filename, dependencies)
  if (filename.endsWith('.css')) {
    for (const match of text.matchAll(/@import\s+['"]([^'"]+)['"]/g)) dependencies.push(resolve(filename, match[1]))
    continue
  }
  try {
    let script = text, descriptor
    if (filename.endsWith('.vue')) {
      const parsed = parseVue(text, { filename })
      if (parsed.errors.length) throw parsed.errors[0]
      descriptor = parsed.descriptor
      script = descriptor.scriptSetup?.content || descriptor.script?.content || ''
      for (const style of descriptor.styles) if (style.src) dependencies.push(resolve(filename, style.src))
    }
    const ast = parseJs(script, { sourceType: 'module' }), componentImports = []
    walk(ast, node => {
      const reference = node.type === 'ImportDeclaration' ? node.source.value
        : node.type === 'CallExpression' && node.callee.type === 'Import' ? node.arguments[0]?.value : null
      if (reference) dependencies.push(resolve(filename, reference))
      if (node.type === 'ImportDeclaration' && node.source.value.endsWith('.vue')) componentImports.push(...node.specifiers.map(s => s.local.name))
      if (filename.endsWith(`${path.sep}api.js`) && node.type === 'VariableDeclarator' && node.id.name === 'api') {
        for (const method of node.init.properties) apiMethods.add(method.key.name)
      }
      if (node.type === 'MemberExpression' && node.object.type === 'Identifier' && node.object.name === 'api') {
        const name = node.computed ? node.property.value : node.property.name
        if (name) apiCalls.push({ filename, name })
      }
    })
    if (descriptor?.scriptSetup) {
      const compiled = compileScript(descriptor, { id: 'source-check' })
      const template = compileTemplate({ source: descriptor.template?.content || '', filename, id: 'source-check', compilerOptions: { bindingMetadata: compiled.bindings } })
      if (template.errors.length) throw template.errors[0]
      const references = new Set()
      const visit = node => { if (node.type === 'Identifier') references.add(node.name); if (node.type === 'StringLiteral') references.add(node.value) }
      ast.program.body.filter(n => n.type !== 'ImportDeclaration').forEach(n => walk(n, visit))
      walk(parseJs(template.code, { sourceType: 'module' }), node => {
        visit(node)
        if (node.type === 'MemberExpression' && node.object.type === 'Identifier' && node.object.name === '_ctx') {
          const name = node.computed ? node.property.value : node.property.name
          if (name && !name.startsWith('$') && !(name in compiled.bindings)) problems.push(`${path.relative(root, filename)}: undefined template binding ${name}`)
        }
      })
      for (const name of componentImports) if (!references.has(name)) problems.push(`${path.relative(root, filename)}: unused component ${name}`)
    }
  } catch (error) { problems.push(`${path.relative(root, filename)}: ${error.message || error}`) }
}

const reached = new Set()
function reach(filename) {
  if (!filename || reached.has(filename)) return
  reached.add(filename)
  for (const child of graph.get(filename) || []) reach(child)
}
reach(path.join(sourceRoot, 'main.js'))
for (const filename of graph.keys()) if (!reached.has(filename)) problems.push(`${path.relative(root, filename)}: unreachable from main.js`)
for (const { filename, name } of apiCalls) if (!apiMethods.has(name)) problems.push(`${path.relative(root, filename)}: unknown API wrapper ${name}`)
if (problems.length) { console.error(problems.join('\n')); process.exitCode = 1 }
else console.log(`PASS: ${graph.size} source files reachable; component imports and local references checked.`)
