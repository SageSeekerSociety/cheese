#!/usr/bin/env node
// Split check: a watch/watchEffect source removed from an SFC must survive in the
// composable the code moved into (or in another SFC the same change touched).
//
// Left behind silently, such a watcher only shows when the host instance is reused
// and the parent swaps state under it: nothing fails, the data still loads, and a
// menu keeps showing the previous topic's numbers. This compares the source
// expressions on both sides of the split.
//
// Usage: node frontend/scripts/composable-watch-guard.mjs [--repo DIR] [--base REF]
//        [--head REF | --staged] [--composable PATH]... [--allow PATH:KEY]...
//        | --self-test
// Default: merge-base(origin/main, HEAD) -> working tree, including untracked files.
// Exit 0: no watch source left behind; 1: at least one; 2: cannot judge.
//
// Equivalence is bounded, not general expression equivalence: both sides are
// canonicalized to prop paths (`props.x` and `toValue(scope.x)` match when the call
// site maps `x: () => props.x`), refs are unwrapped, `.value` is dropped, and array
// sources are compared element by element. A renamed or restructured source can be
// reported even though it survived; waive that one explicitly with --allow.
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { parse as parseSfc } from '@vue/compiler-sfc'
import ts from 'typescript'

const MAX_ALTERNATIVES = 32

function git(repo, ...args) {
  return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
}

function unwrap(node) {
  while (
    ts.isParenthesizedExpression(node) ||
    ts.isAsExpression(node) ||
    ts.isTypeAssertionExpression(node) ||
    ts.isNonNullExpression(node) ||
    ts.isSatisfiesExpression(node)
  ) {
    node = node.expression
  }
  return node
}

function flatten(text) {
  return text.replace(/\s+/g, ' ').trim()
}

function product(lists, join) {
  let combinations = [[]]
  for (const list of lists) {
    const next = []
    for (const prefix of combinations) for (const value of list) next.push([...prefix, value])
    combinations = next.slice(0, MAX_ALTERNATIVES)
  }
  return new Set(combinations.map(join))
}

function mapSet(set, fn) {
  return new Set([...set].map(fn))
}

// A member chain as `{ root, path }`, or null when the node is not one.
function memberPath(node) {
  node = unwrap(node)
  if (ts.isIdentifier(node)) return { root: node.text, path: [] }
  if (ts.isPropertyAccessExpression(node)) {
    const base = memberPath(node.expression)
    if (!base) return null
    base.path.push(node.name.text)
    return base
  }
  if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) {
    const base = memberPath(node.expression)
    if (!base) return null
    base.path.push(node.argumentExpression.text)
    return base
  }
  return null
}

function propertyName(node) {
  if (ts.isIdentifier(node) || ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return node.text
  return null
}

// What a source expression watches, in the terms the other side of the split uses.
// `ctx` carries the scope the host handed the composable:
//   scopeParam       the composable's first parameter, when it is a plain identifier
//   scopeProps       scope property -> source keys the changed call sites pass for it
//   scopeWholeProps  a call site passes the props object itself
//   bindings         destructured scope names -> source keys the call sites pass
function memberKeys(node, ctx) {
  const member = memberPath(node)
  if (!member) return null
  const segments = member.path.filter((segment) => segment !== 'value')
  const { root } = member
  if (root === 'props') return new Set([segments.length ? segments.join('.') : 'props'])
  if (ctx.scopeParam && root === ctx.scopeParam) {
    if (!segments.length) return new Set(['$scope'])
    const [head, ...rest] = segments
    const mapped = ctx.scopeProps?.get(head)
    if (mapped) return new Set([...mapped].map((key) => (rest.length ? `${key}.${rest.join('.')}` : key)))
    if (ctx.scopeWholeProps) return new Set([segments.join('.')])
    return new Set([`$scope.${segments.join('.')}`])
  }
  if (ctx.bindings?.has(root)) {
    const mapped = ctx.bindings.get(root)
    return new Set([...mapped].map((key) => (segments.length ? `${key}.${segments.join('.')}` : key)))
  }
  return new Set([segments.length ? `${root}.${segments.join('.')}` : root])
}

function canonicalFunction(node, ctx) {
  if (!ts.isBlock(node.body)) return canonical(node.body, ctx)
  const statements = node.body.statements
  if (statements.length === 1 && ts.isReturnStatement(statements[0]) && statements[0].expression) {
    return canonical(statements[0].expression, ctx)
  }
  return mapSet(canonicalStatements(statements, ctx), (text) => `{${text}}`)
}

function canonicalStatements(statements, ctx) {
  return product(
    statements.map((statement) => canonicalStatement(statement, ctx)),
    (parts) => parts.join(';')
  )
}

function brace(text) {
  return text.startsWith('{') ? text : `{${text}}`
}

function canonicalStatement(node, ctx) {
  node = unwrap(node)
  if (ts.isExpressionStatement(node)) return canonical(node.expression, ctx)
  if (ts.isReturnStatement(node)) {
    return node.expression ? mapSet(canonical(node.expression, ctx), (text) => `return ${text}`) : new Set(['return'])
  }
  if (ts.isVariableStatement(node)) {
    return product(
      node.declarationList.declarations.map((declaration) => {
        const name = flatten(declaration.name.getText())
        return declaration.initializer
          ? mapSet(canonical(declaration.initializer, ctx), (text) => `${name}=${text}`)
          : new Set([name])
      }),
      (parts) => `let ${parts.join(',')}`
    )
  }
  if (ts.isBlock(node)) return mapSet(canonicalStatements(node.statements, ctx), (text) => `{${text}}`)
  if (ts.isIfStatement(node)) {
    const elsePart = node.elseStatement ? canonicalStatement(node.elseStatement, ctx) : new Set([''])
    return product([canonical(node.expression, ctx), canonicalStatement(node.thenStatement, ctx), elsePart], (parts) => {
      const elseText = parts[2]
        ? ts.isIfStatement(unwrap(node.elseStatement))
          ? `else ${parts[2]}`
          : `else${brace(parts[2])}`
        : ''
      return `if(${parts[0]})${brace(parts[1])}${elseText}`
    })
  }
  return new Set([flatten(node.getText())])
}

function canonical(node, ctx = {}) {
  node = unwrap(node)
  const members = memberKeys(node, ctx)
  if (members) return members
  if (ts.isCallExpression(node)) {
    const callee = unwrap(node.expression)
    if (ts.isIdentifier(callee) && (callee.text === 'toValue' || callee.text === 'unref') && node.arguments.length === 1) {
      return canonical(node.arguments[0], ctx)
    }
    const parts = [canonical(node.expression, ctx), ...node.arguments.map((argument) => canonical(argument, ctx))]
    return product(parts, (values) => `${values[0]}(${values.slice(1).join(',')})`)
  }
  if (ts.isArrayLiteralExpression(node)) {
    return product(
      node.elements.map((element) => canonical(element, ctx)),
      (parts) => `[${parts.join(',')}]`
    )
  }
  if (ts.isObjectLiteralExpression(node)) {
    const fields = node.properties.map((property) => {
      if (ts.isPropertyAssignment(property)) {
        const name = propertyName(property.name)
        if (name === null) return new Set([flatten(property.getText())])
        return product([new Set([name]), canonical(property.initializer, ctx)], (parts) => `${parts[0]}:${parts[1]}`)
      }
      if (ts.isShorthandPropertyAssignment(property)) return new Set([property.name.text])
      return new Set([flatten(property.getText())])
    })
    return product(fields, (parts) => `{${parts.join(',')}}`)
  }
  if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) return canonicalFunction(node, ctx)
  if (ts.isBinaryExpression(node)) {
    return product(
      [canonical(node.left, ctx), canonical(node.right, ctx)],
      (parts) => `${parts[0]} ${node.operatorToken.getText()} ${parts[1]}`
    )
  }
  if (ts.isConditionalExpression(node)) {
    return product(
      [canonical(node.condition, ctx), canonical(node.whenTrue, ctx), canonical(node.whenFalse, ctx)],
      (parts) => `${parts[0]} ? ${parts[1]} : ${parts[2]}`
    )
  }
  if (ts.isPrefixUnaryExpression(node)) return mapSet(canonical(node.operand, ctx), (text) => `${ts.tokenToString(node.operator)}${text}`)
  if (ts.isTypeOfExpression(node)) return mapSet(canonical(node.expression, ctx), (text) => `typeof ${text}`)
  if (ts.isAwaitExpression(node)) return mapSet(canonical(node.expression, ctx), (text) => `await ${text}`)
  if (ts.isStringLiteralLike(node)) return new Set([JSON.stringify(node.text)])
  if (ts.isNumericLiteral(node)) return new Set([node.text])
  if (node.kind === ts.SyntaxKind.TrueKeyword || node.kind === ts.SyntaxKind.FalseKeyword) {
    return new Set([node.kind === ts.SyntaxKind.TrueKeyword ? 'true' : 'false'])
  }
  if (node.kind === ts.SyntaxKind.NullKeyword) return new Set(['null'])
  if (ts.isIdentifier(node) && node.text === 'undefined') return new Set(['undefined'])
  return new Set([flatten(node.getText())])
}

function vueWatchBindings(sourceFile) {
  const bindings = new Map()
  const namespaces = new Set()
  for (const statement of sourceFile.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue
    if (statement.moduleSpecifier.text !== 'vue' && statement.moduleSpecifier.text !== 'vue-demi') continue
    const clause = statement.importClause
    if (!clause?.namedBindings) continue
    if (ts.isNamespaceImport(clause.namedBindings)) {
      namespaces.add(clause.namedBindings.name.text)
      continue
    }
    for (const element of clause.namedBindings.elements) {
      const imported = (element.propertyName ?? element.name).text
      if (imported === 'watch' || imported === 'watchEffect') bindings.set(element.name.text, imported)
    }
  }
  return { bindings, namespaces }
}

function keysToItems(keys, node) {
  return [...keys].map((key) => ({ key, text: flatten(node.getText()) }))
}

function watchSourceItems(node, ctx) {
  node = unwrap(node)
  if (ts.isArrayLiteralExpression(node)) return node.elements.flatMap((element) => watchSourceItems(element, ctx))
  if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
    if (!ts.isBlock(node.body)) {
      const body = unwrap(node.body)
      if (ts.isArrayLiteralExpression(body)) return body.elements.flatMap((element) => watchSourceItems(element, ctx))
      return keysToItems(canonical(body, ctx), body)
    }
    if (node.body.statements.length === 1 && ts.isReturnStatement(node.body.statements[0]) && node.body.statements[0].expression) {
      return watchSourceItems(node.body.statements[0].expression, ctx)
    }
  }
  return keysToItems(canonical(node, ctx), node)
}

function canonicalFunctionOrExpression(node, ctx) {
  node = unwrap(node)
  if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) return canonicalFunction(node, ctx)
  return canonical(node, ctx)
}

// ctxFor(start) picks the scope of the exported function a call sits in; watchers
// outside every exported function keep the plain, unmapped reading.
function collectScriptWatchers(sourceFile, ctx, lineBase, ctxFor, out) {
  const { bindings, namespaces } = vueWatchBindings(sourceFile)
  const visit = (node) => {
    const start = node.getStart(sourceFile)
    const scoped = ctxFor ? ctxFor(start) : ctx
    if (ts.isCallExpression(node) && node.arguments.length) {
      const callee = unwrap(node.expression)
      let kind = null
      if (ts.isIdentifier(callee)) kind = bindings.get(callee.text) ?? null
      else if (
        ts.isPropertyAccessExpression(callee) &&
        ts.isIdentifier(callee.expression) &&
        namespaces.has(callee.expression.text) &&
        (callee.name.text === 'watch' || callee.name.text === 'watchEffect')
      ) {
        kind = callee.name.text
      }
      if (kind === 'watch') {
        const line = lineBase + sourceFile.getLineAndCharacterOfPosition(start).line
        for (const item of watchSourceItems(node.arguments[0], scoped)) out.push({ ...item, kind, line })
      } else if (kind === 'watchEffect') {
        const line = lineBase + sourceFile.getLineAndCharacterOfPosition(start).line
        const body = canonicalFunctionOrExpression(node.arguments[0], scoped)
        for (const key of body) out.push({ kind, key: `effect:${key}`, text: flatten(node.arguments[0].getText()), line })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
}

function scriptBlocks(file, source) {
  if (!file.endsWith('.vue')) return [{ text: source, line: 1 }]
  const { descriptor, errors } = parseSfc(source, { filename: file })
  if (errors.length) throw new Error(`${file}: ${errors[0].message ?? errors[0]}`)
  return [descriptor.script, descriptor.scriptSetup]
    .filter(Boolean)
    .map((block) => ({ text: block.content, line: block.loc.start.line }))
}

function parseModule(text, file) {
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
}

function analyzeSfc(file, source) {
  const items = []
  for (const block of scriptBlocks(file, source)) {
    collectScriptWatchers(parseModule(block.text, file), {}, block.line, null, items)
  }
  return items
}

function hasExport(node) {
  return Boolean(node.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword))
}

function exportedFunctionRanges(sourceFile) {
  const found = []
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name && hasExport(statement)) {
      found.push({ name: statement.name.text, node: statement, param: statement.parameters[0] })
    }
    if (ts.isVariableStatement(statement) && hasExport(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue
        const init = declaration.initializer
        if (ts.isArrowFunction(init) || ts.isFunctionExpression(init)) {
          found.push({ name: declaration.name.text, node: init, param: init.parameters[0] })
        }
      }
    }
  }
  return found
}

function scopeContext(found, callSites) {
  const ctx = { ...callSites, bindings: new Map() }
  const param = found.param
  if (!param) return ctx
  if (ts.isIdentifier(param.name)) {
    ctx.scopeParam = param.name.text
  } else if (ts.isObjectBindingPattern(param.name)) {
    for (const element of param.name.elements) {
      if (!ts.isIdentifier(element.name)) continue
      const source = element.propertyName ? propertyName(element.propertyName) : element.name.text
      const mapped = callSites.scopeProps?.get(source)
      if (mapped) ctx.bindings.set(element.name.text, mapped)
    }
  }
  return ctx
}

function callSiteMappings(vueFiles, fnName, readHead) {
  const scopeProps = new Map()
  let scopeWholeProps = false
  const add = (key, keys) => {
    if (!scopeProps.has(key)) scopeProps.set(key, new Set())
    for (const value of keys) scopeProps.get(key).add(value)
  }
  for (const file of vueFiles) {
    let source
    try {
      source = readHead(file)
    } catch {
      continue
    }
    if (!source.includes(fnName)) continue
    let blocks
    try {
      blocks = scriptBlocks(file, source)
    } catch {
      continue
    }
    for (const block of blocks) {
      const visit = (node) => {
        const callee = ts.isCallExpression(node) ? unwrap(node.expression) : null
        if (callee && ts.isIdentifier(callee) && callee.text === fnName && node.arguments.length) {
          const argument = unwrap(node.arguments[0])
          if (ts.isObjectLiteralExpression(argument)) {
            for (const property of argument.properties) {
              if (ts.isPropertyAssignment(property)) {
                const name = propertyName(property.name)
                if (name !== null) add(name, canonical(property.initializer))
              } else if (ts.isShorthandPropertyAssignment(property)) {
                add(property.name.text, canonical(property.name))
              }
            }
          } else if (ts.isIdentifier(argument) && argument.text === 'props') {
            scopeWholeProps = true
          }
        }
        ts.forEachChild(node, visit)
      }
      visit(parseModule(block.text, file))
    }
  }
  return { scopeProps, scopeWholeProps }
}

function analyzeComposable(file, source, vueFiles, readHead) {
  const sourceFile = parseModule(source, file)
  const items = []
  const scopes = exportedFunctionRanges(sourceFile).map((found) => ({
    start: found.node.getStart(sourceFile),
    end: found.node.getEnd(),
    ctx: scopeContext(found, callSiteMappings(vueFiles, found.name, readHead)),
  }))
  const ctxFor = (start) => scopes.find((scope) => start >= scope.start && start <= scope.end)?.ctx ?? {}
  collectScriptWatchers(sourceFile, {}, 1, ctxFor, items)
  return items
}

function isComposableFile(file) {
  if (!/\.(m?[jt]s)$/.test(file)) return false
  return file.includes('/composables/') || /(^|\/)use[A-Z][^/]*\.(m?[jt]s)$/.test(file)
}

function changedFiles(repo, baseline, target, staged) {
  const args = ['diff', '--name-status', '--no-renames', '-z', baseline]
  if (target) args.push(target)
  if (staged) args.push('--cached')
  const fields = git(repo, ...args).split('\0').filter(Boolean)
  const changed = []
  for (let i = 0; i < fields.length; i += 2) changed.push({ status: fields[i], file: fields[i + 1] })
  if (!target && !staged) {
    for (const file of git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0').filter(Boolean)) {
      changed.push({ status: 'A', file })
    }
  }
  return changed
}

function listVueFiles(repo, target, staged) {
  if (target) {
    return git(repo, 'ls-tree', '-r', '--name-only', '-z', target)
      .split('\0')
      .filter((file) => file.endsWith('.vue'))
  }
  const args = staged ? ['ls-files', '--cached', '-z'] : ['ls-files', '-z']
  const files = git(repo, ...args).split('\0').filter((file) => file.endsWith('.vue'))
  if (!staged) {
    for (const file of git(repo, 'ls-files', '--others', '--exclude-standard', '-z')
      .split('\0')
      .filter((file) => file.endsWith('.vue'))) {
      files.push(file)
    }
  }
  return files
}

function check(options) {
  const repo = git(options.repo, 'rev-parse', '--show-toplevel').trim()
  const resolve = (ref) => git(repo, 'rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`).trim()
  const baseline = options.base ? resolve(options.base) : git(repo, 'merge-base', 'origin/main', 'HEAD').trim()
  const target = options.head ? resolve(options.head) : null
  if (target && options.staged) throw new Error('--head and --staged are mutually exclusive')
  const changed = changedFiles(repo, baseline, target, options.staged)
  const headCache = new Map()
  const readHead = (file) => {
    if (headCache.has(file)) return headCache.get(file)
    let source
    if (target) source = git(repo, 'show', `${target}:${file}`)
    else if (options.staged) source = git(repo, 'show', `:${file}`)
    else {
      const absolute = path.resolve(repo, file)
      if (fs.realpathSync(absolute) !== absolute) throw new Error(`${file}: symlink source cannot be judged`)
      source = fs.readFileSync(absolute, 'utf8')
    }
    headCache.set(file, source)
    return source
  }
  const readBase = (file) => git(repo, 'show', `${baseline}:${file}`)

  const baseItems = new Map()
  const headItems = new Map()
  for (const { status, file } of changed) {
    if (!file.endsWith('.vue')) continue
    if (status === 'M' || status === 'D') baseItems.set(file, analyzeSfc(file, readBase(file)))
    if (status === 'A' || status === 'M') headItems.set(file, analyzeSfc(file, readHead(file)))
  }

  const targets = new Set()
  for (const file of options.composables) targets.add(path.posix.normalize(file.replace(/^\.\//, '')))
  for (const { status, file } of changed) {
    if ((status === 'A' || status === 'M') && isComposableFile(file)) targets.add(file)
  }
  const composableItems = new Map()
  const vueFiles = listVueFiles(repo, target, options.staged)
  for (const file of targets) {
    composableItems.set(file, analyzeComposable(file, readHead(file), vueFiles, readHead))
  }

  const available = new Map()
  const addAvailable = (file, items) => {
    for (const item of items) if (!available.has(item.key)) available.set(item.key, { file, line: item.line })
  }
  for (const [file, items] of headItems) addAvailable(file, items)
  for (const [file, items] of composableItems) addAvailable(file, items)

  const findings = []
  let checked = 0
  for (const [file, items] of baseItems) {
    for (const item of items) {
      checked++
      if (isEquivalent(available, item.key)) continue
      if (options.allows.includes(`${file}:${item.key}`) || options.allows.includes(`${file}:*`)) continue
      findings.push({ file, ...item })
    }
  }
  return { findings, checked, targets: [...targets], baseline }
}

// The comparison every finding goes through. The self-test swaps this line for a
// mutated copy with an over-broad exemption and asserts the contracts go red, so
// keep the line verbatim.
// MUTATION ANCHOR — composable-watch-guard.test.mjs replaces this line.
const isEquivalent = (available, lost) => available.has(lost)

function scopeNameFor(key) {
  return key
    .split('.')
    .map((part, index) => (index === 0 ? part : part.charAt(0).toUpperCase() + part.slice(1)))
    .join('')
}

function fixFor(finding) {
  if (finding.kind === 'watchEffect') {
    return 'fix: move the watchEffect body with the code so it still tracks the same values (pass them through the scope, e.g. toValue(scope.x)); add a test that fails without it.'
  }
  if (finding.key.startsWith('$scope.')) {
    return `fix: the composable watches \`${finding.key.slice(7)}\`, but no call site maps it to a parent value; pass it through the scope and add a test that fails without that watch.`
  }
  const scopeName = scopeNameFor(finding.key)
  return `fix: carry the watcher into the composable, e.g. watch(() => toValue(scope.${scopeName}), ...) with \`${scopeName}: () => props.${finding.key}\` at the call site; add a test that fails without that watch.`
}

function report(findings, result) {
  for (const finding of findings) {
    const kindText = finding.kind === 'watchEffect' ? 'watchEffect' : 'watch source'
    console.error(
      `LOST ${finding.file}:${finding.line} ${kindText} \`${finding.text}\` has no equivalent in the split target.`
    )
    console.error(
      `  targets: ${result.targets.length ? result.targets.join(', ') : '(none changed; pass --composable PATH)'}`
    )
    console.error(`  ${fixFor(finding)}`)
    console.error(`  waive: --allow '${finding.file}:${finding.key}'`)
  }
  const summary = `${findings.length ? 'FAIL' : 'PASS (composable watch guard)'}: ${findings.length} watch source(s) left behind; ${result.checked} checked against ${result.targets.length} target composable(s); base ${result.baseline.slice(0, 8)}`
  console.log(summary)
}

function main(argv) {
  if (argv.length === 1 && argv[0] === '--self-test') {
    return (
      spawnSync(process.execPath, ['--test', fileURLToPath(new URL('./composable-watch-guard.test.mjs', import.meta.url))], {
        stdio: 'inherit',
      }).status ?? 2
    )
  }
  if (argv.includes('--help')) {
    console.log(
      'composable-watch-guard.mjs [--repo DIR] [--base REF] [--head REF | --staged] [--composable PATH]... [--allow PATH:KEY]... | --self-test\n0 = no watch source left behind; 1 = at least one; 2 = cannot judge.'
    )
    return 0
  }
  const options = { repo: process.cwd(), composables: [], allows: [] }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--staged') options.staged = true
    else if ((arg === '--composable' || arg === '--allow') && argv[i + 1] && !argv[i + 1].startsWith('--')) {
      options[arg.slice(2) + 's'].push(argv[++i])
    } else if (['--repo', '--base', '--head'].includes(arg) && argv[i + 1] && !argv[i + 1].startsWith('--')) {
      options[arg.slice(2)] = argv[++i]
    } else throw new Error(`unknown or incomplete argument: ${arg}`)
  }
  const result = check(options)
  report(result.findings, result)
  return result.findings.length ? 1 : 0
}

try {
  process.exitCode = main(process.argv.slice(2))
} catch (error) {
  console.error(`CANNOT JUDGE: ${error.message}`)
  process.exitCode = 2
}
