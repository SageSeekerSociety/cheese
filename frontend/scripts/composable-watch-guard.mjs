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
// canonicalized to prop paths. `props.x` and `toValue(scope.x)` match when the call
// site maps `x: () => props.x`, or passes the scope as `() => ({ x: props.x })`; a
// composable taking the value itself (`fn(() => props.x)`) matches `toValue(x)`; refs,
// `computed(() => props.x)` and local aliases (`const s = toValue(scope)`) are
// unwrapped, and array sources are compared element by element. A ref's `.value` is
// dropped where the root is a ref: a local name, or a parameter handed a ref. A prop
// path, a scope path, and a destructured binding keep their segments as written, so
// `props.x` and `props.x.value` do not stand in for each other. Keys rooted outside
// props and the scope live under `$local.`, so a local ref never stands in for a lost
// prop. A renamed or restructured source can be reported even though it survived;
// waive that one with --allow.
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

// A member chain as `{ root, path }`, or null when the node is not one. `toValue`
// and `unref` are transparent, and a local `const s = toValue(scope)` is followed
// through `ctx.aliases` so both sides of the split speak in the same terms.
function memberPath(node, ctx = {}) {
  node = unwrap(node)
  if (ts.isCallExpression(node)) {
    const callee = unwrap(node.expression)
    if (
      ts.isIdentifier(callee) &&
      (callee.text === 'toValue' || callee.text === 'unref' || callee.text === 'computed') &&
      node.arguments.length === 1
    ) {
      // `computed(() => props.x)` watches the same thing the inner expression does.
      const argument = unwrap(node.arguments[0])
      const getter = ts.isArrowFunction(argument) || ts.isFunctionExpression(argument)
      const value = getter ? unwrap(argument.body) : argument
      const inner = memberPath(value, ctx)
      // `toValue(config).value` names a field: the call already unwrapped one level.
      // What it unwrapped is the path it was given, so the depth is recorded and not
      // just a flag: `toValue(source).topicId.value` unwrapped the scope container,
      // not `topicId`, and the ref in `topicId` still unwraps. A getter writes its
      // accesses inside the callback, where they unwrap as usual
      // (`computed(() => x.value)` watches x), so its path keeps its own reading.
      // `unwrappedAt` is the earliest argument end of a transparent call on the
      // chain, i.e. where fields start; `argEnd` stays the last call's own argument
      // end, so a property can tell whether a call ended on it.
      if (inner && !getter) {
        const end = inner.path.length
        if (inner.unwrappedAt === undefined || end < inner.unwrappedAt) inner.unwrappedAt = end
        inner.argEnd = end
      }
      return inner
    }
    return null
  }
  if (ts.isIdentifier(node)) {
    const alias = ctx.aliases?.get(node.text)
    if (alias)
      return {
        root: alias.root,
        path: [...alias.path],
        unwrappedAt: alias.unwrappedAt,
        argEnd: alias.argEnd,
      }
    return { root: node.text, path: [] }
  }
  if (ts.isPropertyAccessExpression(node)) {
    const base = memberPath(node.expression, ctx)
    if (!base) return null
    base.path.push(node.name.text)
    return base
  }
  if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) {
    const base = memberPath(node.expression, ctx)
    if (!base) return null
    base.path.push(node.argumentExpression.text)
    return base
  }
  return null
}

// Local `const x = <member chain>` declarations inside a scope, so a getter that
// names the scope once and reads it through locals still resolves.
function collectAliases(root, ctx) {
  const bind = (name, member) => {
    if (member && (member.root !== name || member.path.length)) ctx.aliases.set(name, member)
  }
  const visit = (node) => {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      const member = memberPath(node.initializer, ctx)
      if (ts.isIdentifier(node.name)) bind(node.name.text, member)
      // `const { topicId } = props` names the prop path too.
      else if (
        member &&
        ts.isObjectBindingPattern(node.name) &&
        (member.root === 'props' || member.root === ctx.scopeParam)
      ) {
        for (const element of node.name.elements) {
          if (element.dotDotDotToken || !ts.isIdentifier(element.name)) continue
          const key = element.propertyName ? propertyName(element.propertyName) : element.name.text
          if (key !== null)
            bind(element.name.text, {
              root: member.root,
              path: [...member.path, key],
              unwrappedAt: member.unwrappedAt,
              argEnd: member.argEnd,
            })
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(root)
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
//   scopeValue       the value itself when the composable's first parameter is it,
//                    not a scope object (`fn(() => props.sid)`), mapped to whether
//                    the call site handed over a ref
//   bindings         destructured scope names -> source keys the call sites pass
//   aliases          local names bound to a member chain inside the scope
// Each call site is read the way it hands the parameter over: a ref (`fn(topicId)`,
// `fn(computed(() => props.x))`) makes `.value` on the parameter the unwrap, a plain
// prop expression (`fn(() => props.x)`) makes it a field. Call sites that disagree
// each keep their own reading, so one caller cannot decide for another.
//
// One `.value` is the unwrap and nothing past it is: `x.value` is x's value, and its
// `.value` names a field. An expression that already unwrapped — `toValue(x)`, or a
// parameter a call site handed a ref — is at the value already, so every `.value`
// written there names a field. A `$local.` key names a local's value the same way the
// component writes it.
function refSegments(segments, asRef, unwrappedAt, consumed = 0) {
  // `unwrappedAt` is how far a transparent call unwrapped the chain. A walk that has
  // consumed exactly that many segments is at the value the call returned: every
  // further `.value` names a field, not an unwrap. Before that depth the chain is
  // read as written — a container may have been unwrapped while the property on it
  // is still whatever its call site handed over.
  if (typeof unwrappedAt === 'number' && unwrappedAt - consumed === 0) return segments
  if (!asRef || segments[0] !== 'value') return segments
  return segments.slice(1)
}

function mapSegments(keys, segments, unwrappedAt, consumed = 0) {
  const out = new Set()
  for (const key of keys) {
    const parts = refSegments(segments, key.startsWith('$local.'), unwrappedAt, consumed)
    out.add(parts.length ? `${key}.${parts.join('.')}` : key)
  }
  return out
}

function memberKeys(node, ctx) {
  const member = memberPath(node, ctx)
  if (!member) return null
  const { root, unwrappedAt, argEnd } = member
  // Both sides of the split must spell the same path for `props.config` and
  // `props.config.value` to stay apart: a prop path keeps its segments as written.
  const path = [...member.path]
  if (root === 'props') return new Set([path.length ? path.join('.') : 'props'])
  if (ctx.scopeParam && root === ctx.scopeParam) {
    const out = new Set()
    if (path.length) {
      const [head, ...rest] = path
      const mapped = ctx.scopeProps?.get(head)
      // A property the call sites hand over as a getter is a function here: reading
      // it without a transparent call that ends on it (`toValue(scope.x)`) yields the
      // function, and nothing watched through it. `head` is this call's own segment,
      // so an unwrap recorded at its depth belongs to it.
      const invoked = argEnd === 1
      if (mapped && !(ctx.scopePropGetter?.get(head) && !invoked)) {
        for (const key of mapSegments(mapped, rest, unwrappedAt, 1)) out.add(key)
      }
      // The props object itself was handed over: the segments are prop names.
      if (ctx.scopeWholeProps) out.add(path.join('.'))
    } else if (ctx.scopeWholeProps) {
      out.add('props')
    }
    for (const [key, asRef] of ctx.scopeValue ?? []) {
      const parts = refSegments(path, asRef, unwrappedAt)
      out.add(parts.length ? `${key}.${parts.join('.')}` : key)
    }
    if (out.size) return out
    return new Set([path.length ? `$scope.${path.join('.')}` : '$scope'])
  }
  if (ctx.bindings?.has(root)) return mapSegments(ctx.bindings.get(root), path, unwrappedAt)
  // Local names get their own namespace: a ref called `topicId` must not stand in
  // for a lost `props.topicId` watch.
  const parts = refSegments(path, true, unwrappedAt)
  return new Set([parts.length ? `$local.${root}.${parts.join('.')}` : `$local.${root}`])
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
    return product(
      [canonical(node.expression, ctx), canonicalStatement(node.thenStatement, ctx), elsePart],
      (parts) => {
        const elseText = parts[2]
          ? ts.isIfStatement(unwrap(node.elseStatement))
            ? `else ${parts[2]}`
            : `else${brace(parts[2])}`
          : ''
        return `if(${parts[0]})${brace(parts[1])}${elseText}`
      }
    )
  }
  return new Set([flatten(node.getText())])
}

function canonical(node, ctx = {}) {
  node = unwrap(node)
  const members = memberKeys(node, ctx)
  if (members) return members
  if (ts.isCallExpression(node)) {
    const callee = unwrap(node.expression)
    if (
      ts.isIdentifier(callee) &&
      (callee.text === 'toValue' || callee.text === 'unref') &&
      node.arguments.length === 1
    ) {
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
  if (ts.isPrefixUnaryExpression(node))
    return mapSet(canonical(node.operand, ctx), (text) => `${ts.tokenToString(node.operator)}${text}`)
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
    // A getter that only names locals and returns one expression watches that
    // expression: `() => { const s = toValue(source); return [s.a, s.b] }`.
    const statements = node.body.statements
    const last = statements[statements.length - 1]
    if (
      last &&
      ts.isReturnStatement(last) &&
      last.expression &&
      statements.slice(0, -1).every((statement) => ts.isVariableStatement(statement))
    ) {
      return watchSourceItems(last.expression, ctx)
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
        for (const key of body)
          out.push({ kind, key: `effect:${key}`, text: flatten(node.arguments[0].getText()), line })
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
    const sourceFile = parseModule(block.text, file)
    const ctx = { aliases: new Map() }
    collectAliases(sourceFile, ctx)
    collectScriptWatchers(sourceFile, ctx, block.line, null, items)
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
  const ctx = { ...callSites, bindings: new Map(), aliases: new Map() }
  const param = found.param
  if (param) {
    if (ts.isIdentifier(param.name)) {
      ctx.scopeParam = param.name.text
    } else if (ts.isObjectBindingPattern(param.name)) {
      for (const element of param.name.elements) {
        if (!ts.isIdentifier(element.name)) continue
        const source = element.propertyName ? propertyName(element.propertyName) : element.name.text
        if (source === null) continue
        const mapped = callSites.scopeProps?.get(source)
        if (mapped) ctx.bindings.set(element.name.text, mapped)
        // A call site that hands over the whole props object destructures the prop
        // itself, so the local name is the prop path.
        else if (callSites.scopeWholeProps) ctx.bindings.set(element.name.text, new Set([source]))
      }
    }
  }
  ctx.scopePropGetter = callSites.scopePropGetter
  collectAliases(found.node, ctx)
  return ctx
}

// Vue's ref factories return a ref whatever the expression they wrap reads, so a call
// site that hands one over hands over a ref.
const REF_FACTORIES = new Set(['computed', 'ref', 'shallowRef', 'toRef', 'customRef'])

function isRefArgument(node) {
  node = unwrap(node)
  if (!ts.isCallExpression(node)) return false
  const callee = unwrap(node.expression)
  return ts.isIdentifier(callee) && REF_FACTORIES.has(callee.text)
}

function callSiteMappings(vueFiles, fnName, readHead) {
  const scopeProps = new Map()
  // Property -> whether every call site hands it as a function (a getter). The
  // composable only reaches such a property's value through a call.
  const scopePropGetter = new Map()
  const scopeValue = new Map()
  let scopeWholeProps = false
  const add = (key, keys, getter) => {
    if (!scopeProps.has(key)) {
      scopeProps.set(key, new Set())
      scopePropGetter.set(key, true)
    }
    if (!getter) scopePropGetter.set(key, false)
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
          // A call site may hand over the scope as a getter: `fn(() => ({ x: props.x }))`
          // is the same scope as `fn({ x: () => props.x })`.
          const target =
            ts.isArrowFunction(argument) || ts.isFunctionExpression(argument) ? unwrap(argument.body) : argument
          const scopeObject = ts.isObjectLiteralExpression(target) ? target : null
          if (scopeObject) {
            for (const property of scopeObject.properties) {
              if (ts.isPropertyAssignment(property)) {
                const name = propertyName(property.name)
                const initializer = unwrap(property.initializer)
                const getter = ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer)
                if (name !== null) add(name, canonical(property.initializer), getter)
              } else if (ts.isShorthandPropertyAssignment(property)) {
                add(property.name.text, canonical(property.name))
              }
            }
          } else if (ts.isIdentifier(target) && target.text === 'props') {
            scopeWholeProps = true
          } else {
            // The parameter is the value itself: `fn(() => props.sid)`, watched as
            // `toValue(sid)` on the composable side. A call site that hands over a ref
            // rather than the value keeps its own reading per key.
            const asRef = isRefArgument(argument)
            for (const key of canonical(argument)) {
              scopeValue.set(key, Boolean(scopeValue.get(key)) || asRef || key.startsWith('$local.'))
            }
          }
        }
        ts.forEachChild(node, visit)
      }
      visit(parseModule(block.text, file))
    }
  }
  return { scopeProps, scopePropGetter, scopeWholeProps, scopeValue }
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
  const fields = git(repo, ...args)
    .split('\0')
    .filter(Boolean)
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
  const files = git(repo, ...args)
    .split('\0')
    .filter((file) => file.endsWith('.vue'))
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
      if (isWaived(options.allows, file, item.key)) continue
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

// The --allow waiver. Anchored like the comparison: the self-test swaps it for
// copies that ignore the file or the key and asserts the wrong waivers stop passing.
// MUTATION ANCHOR (allow) — composable-watch-guard.test.mjs replaces this line.
const isWaived = (allows, file, key) => allows.includes(`${file}:${key}`) || allows.includes(`${file}:*`)

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
  if (finding.key.startsWith('$local.')) {
    return `fix: the watcher tracked a value local to the component (\`${finding.key.slice(7)}\`), not a prop; carry it with the code, pass that value into the composable, and add a test that fails without that watch.`
  }
  const scopeName = scopeNameFor(finding.key)
  return `fix: carry the watcher into the composable, e.g. watch(() => toValue(scope.${scopeName}), ...) with \`${scopeName}: () => props.${finding.key}\` at the call site (or watch(() => toValue(<param>), ...) when the parameter is the value); add a test that fails without that watch.`
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
      spawnSync(
        process.execPath,
        ['--test', fileURLToPath(new URL('./composable-watch-guard.test.mjs', import.meta.url))],
        {
          stdio: 'inherit',
        }
      ).status ?? 2
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
