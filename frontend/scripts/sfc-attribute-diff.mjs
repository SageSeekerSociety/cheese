#!/usr/bin/env node
// Bounded split check: false fallbacks must survive on the same template element.
// Usage: node frontend/scripts/sfc-attribute-diff.mjs [--repo DIR] [--base REF]
//        [--head REF | --staged] | --self-test
// Default: merge-base(origin/main, HEAD) -> working tree, including untracked SFCs.
// Exit 0: no matched fallback regression; 1: regression; 2: cannot judge.
// This is not general expression equivalence: changed payloads are not matched.
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { parse as parseSfc } from '@vue/compiler-sfc'
import ts from 'typescript'

function git(repo, ...args) {
  return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 })
}

function unwrap(node) {
  while (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) ||
    ts.isTypeAssertionExpression(node) || ts.isNonNullExpression(node) || ts.isSatisfiesExpression(node)) {
    node = node.expression
  }
  return node
}

function expression(text) {
  const source = ts.createSourceFile('binding.ts', `const value = (${text});`, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  if (source.parseDiagnostics.length) throw new Error(`cannot parse binding: ${text}`)
  return unwrap(source.statements[0].declarationList.declarations[0].initializer)
}

// Optional calls and guarded calls have the same payload, but not the same fallback.
function fingerprint(node) {
  node = unwrap(node)
  if (ts.isPropertyAccessExpression(node)) return ['member', fingerprint(node.expression), node.name.text]
  if (ts.isElementAccessExpression(node)) return ['index', fingerprint(node.expression), fingerprint(node.argumentExpression)]
  if (ts.isCallExpression(node)) return ['call', fingerprint(node.expression), node.arguments.map(fingerprint)]
  if (ts.isIdentifier(node)) return ['name', node.text]
  if (ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return [node.kind, node.text]
  const children = []
  ts.forEachChild(node, (child) => { children.push(fingerprint(child)) })
  return [node.kind, ...children]
}

function fallback(node) {
  node = unwrap(node)
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken) {
    const right = unwrap(node.right)
    if (right.kind === ts.SyntaxKind.FalseKeyword || (ts.isStringLiteral(right) && right.text === 'false')) {
      return { payload: node.left, repair: ts.isStringLiteral(right) ? "?? 'false'" : '?? false' }
    }
  }
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'Boolean' && node.arguments.length === 1) {
    return { payload: node.arguments[0], repair: 'Boolean(...)', boolean: true }
  }
  return null
}

function absent(node) {
  node = unwrap(node)
  return (ts.isIdentifier(node) && node.text === 'undefined') ||
    (ts.isVoidExpression(node) && ts.isNumericLiteral(unwrap(node.expression)) && unwrap(node.expression).text === '0')
}

function lostFallback(before, after) {
  const old = fallback(expression(before))
  const current = expression(after)
  if (!old || !ts.isConditionalExpression(current)) return null
  const branches = absent(current.whenFalse) ? [current.whenTrue] : absent(current.whenTrue) ? [current.whenFalse] : []
  const expected = JSON.stringify(fingerprint(old.payload))
  for (let branch of branches) {
    branch = unwrap(branch)
    if (old.boolean && fallback(branch)?.boolean) branch = fallback(branch).payload
    if (JSON.stringify(fingerprint(branch)) === expected) return old.repair
  }
  return null
}

function componentName(tag) {
  return tag.replace(/-/g, '').toLowerCase()
}

function analyze(source, file) {
  const { descriptor, errors } = parseSfc(source, { filename: file })
  if (errors.length) throw new Error(`${file}: ${String(errors[0])}`)
  if (descriptor.template?.src) throw new Error(`${file}: external template cannot be judged`)
  const bindings = [], tags = new Set()
  function visit(node) {
    if (node.type === 1) {
      tags.add(componentName(node.tag))
      // Stable element anchors disambiguate repeated tags; class/style can change in a split.
      const anchors = Object.fromEntries(node.props.filter((p) => p.type === 6 &&
        ['id', 'ref', 'role', 'type', 'name', 'data-testid'].includes(p.name)).map((p) => [p.name, p.value?.content ?? '']))
      for (const prop of node.props) {
        if (prop.type !== 7 || prop.name !== 'bind' || !prop.exp) continue
        const attr = prop.arg ? prop.arg.isStatic ? prop.arg.content : `[${prop.arg.content}]` : '(v-bind)'
        // Parse every binding, including non-ARIA attributes. No regex over templates.
        const value = prop.exp.content
        const key = JSON.stringify(fingerprint(expression(value)))
        bindings.push({ file, line: prop.loc.start.line,
          tag: node.tag, anchors, attr, value, key })
      }
    }
    for (const child of node.children ?? []) visit(child)
  }
  if (descriptor.template) visit(descriptor.template.ast)
  const imports = []
  for (const script of [descriptor.script, descriptor.scriptSetup].filter(Boolean)) {
    const ast = ts.createSourceFile(file + '.ts', script.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
    if (ast.parseDiagnostics.length) throw new Error(`${file}: cannot parse script imports`)
    for (const stmt of ast.statements) {
      if (!ts.isImportDeclaration(stmt) || !ts.isStringLiteral(stmt.moduleSpecifier) || !stmt.moduleSpecifier.text.endsWith('.vue')) continue
      const name = stmt.importClause?.name?.text
      if (name && tags.has(componentName(name))) imports.push(stmt.moduleSpecifier.text)
    }
  }
  return { bindings, imports }
}

function compatible(before, after) {
  return before.tag === after.tag && before.attr === after.attr &&
    Object.entries(before.anchors).every(([key, value]) => !(key in after.anchors) || after.anchors[key] === value) &&
    Object.entries(after.anchors).every(([key, value]) => !(key in before.anchors) || before.anchors[key] === value)
}

function pairBindings(before, after) {
  const remaining = [...after], unmatched = []
  // Consume exact matches first, so a surviving duplicate cannot hide a lost fallback.
  for (const binding of before) {
    const i = remaining.findIndex((item) => compatible(binding, item) && binding.key === item.key)
    if (i >= 0) remaining.splice(i, 1)
    else unmatched.push(binding)
  }
  const findings = []
  for (const binding of unmatched) {
    const i = remaining.findIndex((item) => compatible(binding, item) && lostFallback(binding.value, item.value))
    if (i < 0) continue
    const target = remaining.splice(i, 1)[0]
    findings.push({ before: binding, after: target, repair: lostFallback(binding.value, target.value) })
  }
  return findings
}

function resolveImport(file, specifier) {
  if (specifier.startsWith('@/')) return 'frontend/src/' + specifier.slice(2)
  if (specifier.startsWith('.')) return path.posix.normalize(path.posix.join(path.posix.dirname(file), specifier))
  return null
}

function compare({ repo, base, head, staged }) {
  repo = git(repo, 'rev-parse', '--show-toplevel').trim()
  const resolve = (ref) => git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').trim()
  const baseline = base ? resolve(base) : git(repo, 'merge-base', 'origin/main', 'HEAD').trim()
  const target = head ? resolve(head) : null
  const args = ['diff', '--name-status', '--no-renames', '-z', baseline]
  if (target) args.push(target)
  if (staged) args.push('--cached')
  args.push('--', '*.vue')
  const fields = git(repo, ...args).split('\0').filter(Boolean)
  const changed = []
  for (let i = 0; i < fields.length; i += 2) changed.push({ status: fields[i], file: fields[i + 1] })
  if (!target && !staged) {
    for (const file of git(repo, 'ls-files', '--others', '--exclude-standard', '-z', '--', '*.vue').split('\0').filter(Boolean)) {
      changed.push({ status: 'A', file })
    }
  }
  const added = new Set(changed.filter((item) => item.status === 'A').map((item) => item.file))
  const read = (file, old = false) => {
    if (old || target || staged) return git(repo, 'show', `${old ? baseline : target ?? ''}:${file}`)
    const absolute = path.resolve(repo, file)
    if (fs.realpathSync(absolute) !== absolute) throw new Error(`${file}: symlink source cannot be judged`)
    return fs.readFileSync(absolute, 'utf8')
  }
  const findings = []
  let parents = 0
  for (const { status, file } of changed) {
    if (status !== 'M') continue
    const old = read(file, true), current = read(file)
    if (current.length >= old.length) continue
    const original = analyze(old, file), files = new Map()
    function collect(child) {
      if (files.has(child)) return
      const result = analyze(read(child), child)
      files.set(child, result)
      for (const specifier of result.imports) {
        const resolved = resolveImport(child, specifier)
        if (added.has(resolved)) collect(resolved)
      }
    }
    collect(file)
    if (files.size === 1) continue
    parents++
    findings.push(...pairBindings(original.bindings, [...files.values()].flatMap((item) => item.bindings)))
  }
  return { findings, parents, baseline }
}

function main(argv) {
  if (argv.length === 1 && argv[0] === '--self-test') {
    return spawnSync(process.execPath, ['--test', fileURLToPath(new URL('./test-sfc-attribute-diff.mjs', import.meta.url))], { stdio: 'inherit' }).status ?? 2
  }
  if (argv.includes('--help')) {
    console.log('sfc-attribute-diff.mjs [--repo DIR] [--base REF] [--head REF | --staged] | --self-test\n0 = no matched fallback regression; 1 = regression; 2 = cannot judge. Not a general equivalence proof.')
    return 0
  }
  const options = { repo: process.cwd() }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--staged') options.staged = true
    else if (['--repo', '--base', '--head'].includes(arg) && argv[i + 1] && !argv[i + 1].startsWith('--')) options[arg.slice(2)] = argv[++i]
    else throw new Error(`unknown or incomplete argument: ${arg}`)
  }
  if (options.head && options.staged) throw new Error('--head and --staged are mutually exclusive')
  const { findings, parents, baseline } = compare(options)
  for (const { before, after, repair } of findings) {
    console.error(`${before.file}:${before.line} -> ${after.file}:${after.line}: <${before.tag}> :${before.attr} lost ${repair}; undefined drops the explicit fallback.\n  Before: ${before.value}\n  After:  ${after.value}\n  Repair: preserve ${repair}, e.g. :${before.attr}="${before.value}"`)
  }
  console.log(`${findings.length ? 'VIOLATION' : 'PASS (bounded fallback check)'}: ${findings.length} regression(s), ${parents} split parent(s), base ${baseline.slice(0, 8)}`)
  return findings.length ? 1 : 0
}

try {
  process.exitCode = main(process.argv.slice(2))
} catch (error) {
  console.error(`CANNOT JUDGE: ${error.message}`)
  process.exitCode = 2
}
