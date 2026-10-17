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
      // What it unwrapped is the path it was given, so every call's argument end is
      // recorded, not just a flag or a single depth: `toValue(source).topicId.value`
      // unwrapped the scope container, not `topicId`, and the ref in `topicId` still
      // unwraps; `toValue(s.topic).value` unwrapped `topic`, so its `.value` is a
      // field. A getter writes its accesses inside the callback, where they unwrap as
      // usual (`computed(() => x.value)` watches x), so its path keeps its own reading.
      if (inner && !getter) {
        inner.unwrapped = new Set(inner.unwrapped ?? []).add(inner.path.length)
      }
      return inner
    }
    return null
  }
  if (ts.isIdentifier(node)) {
    const alias = aliasAt(ctx, node.text, node.getStart())
    if (alias)
      return {
        root: alias.root,
        path: [...alias.path],
        unwrapped: new Set(alias.unwrapped ?? []),
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

// The local a read of `name` at `pos` sees: the innermost function that contains the
// read, and inside it the latest declaration at or before the read. An alias declared
// in a callback is that callback's local and is not visible outside it.
function aliasAt(ctx, name, pos) {
  let best
  for (const alias of ctx.aliases ?? []) {
    if (alias.name !== name || pos < alias.start || pos > alias.end) continue
    if (!best) {
      best = alias
      continue
    }
    const span = alias.end - alias.start
    const bestSpan = best.end - best.start
    if (span !== bestSpan) {
      if (span < bestSpan) best = alias
      continue
    }
    const before = alias.at <= pos
    const bestBefore = best.at <= pos
    if (before !== bestBefore) {
      if (before) best = alias
      continue
    }
    if (before ? alias.at > best.at : alias.at < best.at) best = alias
  }
  return best?.member
}

// Calls that run the function they are handed again on every change: a local declared
// in one of these names a fresh value each time. The rest of setup (`onMounted`,
// `setTimeout`, a helper called once) runs its callback once, so the same
// destructuring there is a snapshot.
const RERUNNING_CALLS = new Set(['watch', 'watchEffect', 'watchPostEffect', 'watchSyncEffect', 'computed'])

function calleeName(call) {
  const callee = unwrap(call.expression)
  if (ts.isIdentifier(callee)) return callee.text
  if (ts.isPropertyAccessExpression(callee)) return callee.name.text
  return null
}

function isFunctionNode(node) {
  return (
    ts.isArrowFunction(node) ||
    ts.isFunctionExpression(node) ||
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node)
  )
}

// Local `const x = <member chain>` declarations inside a scope, so a getter that
// names the scope once and reads it through locals still resolves. Each alias records
// the function that declares it, so the same name in two scopes stays two names.
function collectAliases(root, ctx) {
  const aliases = (ctx.aliases ??= [])
  const bind = (name, member, scope) => {
    if (member && (member.root !== name.text || member.path.length))
      aliases.push({
        name: name.text,
        member,
        at: name.getStart(),
        start: scope.getStart(),
        end: scope.getEnd(),
      })
  }
  const visit = (node, scope, live) => {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      const member = memberPath(node.initializer, ctx)
      if (ts.isIdentifier(node.name)) bind(node.name, member, scope)
      // `const { topicId } = props` names the prop path too. Destructuring anything
      // else is not a name for the value it came from: `const { value } = toValue(x)`
      // takes a snapshot at setup, and a watch on the local never runs again, so it
      // must not stand in for the watch on the source.
      else if (
        member &&
        ts.isObjectBindingPattern(node.name) &&
        (member.root === 'props' ||
          member.root === ctx.scopeParam ||
          // Inside a callback that reruns on every change the destructuring runs
          // again too, so the local names what that call returned that time: a live
          // read, unlike the setup-time snapshot the same statement takes at the top.
          (live && ctx.bindings?.has(member.root)))
      ) {
        for (const element of node.name.elements) {
          if (element.dotDotDotToken || !ts.isIdentifier(element.name)) continue
          const key = element.propertyName ? propertyName(element.propertyName) : element.name.text
          if (key !== null)
            bind(
              element.name,
              {
                root: member.root,
                path: [...member.path, key],
                unwrapped: new Set(member.unwrapped ?? []),
              },
              scope
            )
        }
      }
    }
    ts.forEachChild(node, (child) => {
      if (isFunctionNode(child)) {
        const reruns = ts.isCallExpression(node) && RERUNNING_CALLS.has(calleeName(node))
        visit(child, child, reruns)
      } else visit(child, scope, live)
    })
  }
  visit(root, root, false)
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
function refSegments(segments, asRef, unwrapped, consumed = 0) {
  // `unwrapped` holds the argument length of every transparent call on the chain. A
  // walk that has consumed exactly that many segments stands on the value the call
  // returned: every further `.value` names a field, not an unwrap. At any other depth
  // the chain is read as written — a container may have been unwrapped while the
  // property on it is still whatever its call site handed over.
  if (unwrapped?.has(consumed)) return segments
  if (!asRef || segments[0] !== 'value') return segments
  return segments.slice(1)
}

function mapSegments(keys, segments, unwrapped, consumed = 0) {
  const out = new Set()
  for (const key of keys) {
    const parts = refSegments(segments, key.startsWith('$local.'), unwrapped, consumed)
    out.add(parts.length ? `${key}.${parts.join('.')}` : key)
  }
  return out
}

// The keys that are the value the composable reads, out of the keys its call sites
// hand over. A key is dropped only when every call site hands it over as a getter:
// one such call site leaves the composable with the function, but a key another
// call site passes as a ref is still read as written there.
function readableKeys(keys, getterOf) {
  if (!keys?.size || !getterOf) return keys
  const out = new Set()
  for (const key of keys) if (!getterOf(key)) out.add(key)
  return out
}

function memberKeys(node, ctx) {
  const member = memberPath(node, ctx)
  if (!member) return null
  const { root, unwrapped } = member
  // Both sides of the split must spell the same path for `props.config` and
  // `props.config.value` to stay apart: a prop path keeps its segments as written.
  const path = [...member.path]
  if (root === 'props') return new Set([path.length ? path.join('.') : 'props'])
  if (ctx.scopeParam && root === ctx.scopeParam) {
    const out = new Set()
    if (path.length) {
      const [head, ...rest] = path
      const mapped = ctx.scopeProps?.get(head)
      // A property every call site hands over as a getter is a function here: reading
      // it without a transparent call that ends on it (`toValue(scope.x)`) yields the
      // function, and nothing watched through it. Any call that landed on `head`
      // (depth 1) reaches the property's value.
      const invoked = unwrapped?.has(1)
      const getterOf = ctx.scopePropGetter?.get(head)
      const keys = invoked ? mapped : readableKeys(mapped, (key) => getterOf?.get(key))
      if (keys?.size) {
        for (const key of mapSegments(keys, rest, unwrapped, 1)) out.add(key)
      }
      // The props object itself was handed over: the segments are prop names.
      if (ctx.scopeWholeProps) out.add(path.join('.'))
    } else if (ctx.scopeWholeProps) {
      out.add('props')
    }
    for (const [key, asRef] of ctx.scopeValue ?? []) {
      const parts = refSegments(path, asRef, unwrapped)
      out.add(parts.length ? `${key}.${parts.join('.')}` : key)
    }
    if (out.size) return out
    return new Set([path.length ? `$scope.${path.join('.')}` : '$scope'])
  }
  const binding = ctx.bindings?.get(root)
  if (binding) {
    // A destructured parameter is the property itself. Handed as a ref, a bare read is
    // its value; handed as a getter function, the binding *is* the function and passing
    // it as the source is Vue's getter-source form, which tracks what the component's
    // own getter tracked. Reading a field off it is neither — `topicId.value` on a
    // function is `undefined` and watches nothing — so those keys are dropped once
    // every call site hands that property as a getter. When no key survives, nothing is
    // watched through it, and the local namespace must not stand in for it.
    const bare = path.length === 0
    const keys =
      unwrapped?.has(0) || (bare && !ctx.inGetter)
        ? binding.keys
        : bare
          ? new Set()
          : readableKeys(binding.keys, (key) => binding.getterKeys?.has(key))
    return mapSegments(keys ?? new Set(), path, unwrapped)
  }
  // Local names get their own namespace: a ref called `topicId` must not stand in
  // for a lost `props.topicId` watch.
  const parts = refSegments(path, true, unwrapped)
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
    // Inside a getter a bare binding is returned, not read: nothing reactive is
    // touched, so the watch tracks nothing. Only a transparent call on it reaches the
    // value the call site handed over.
    const getterCtx = { ...ctx, inGetter: true }
    if (!ts.isBlock(node.body)) {
      const body = unwrap(node.body)
      if (ts.isArrayLiteralExpression(body))
        return body.elements.flatMap((element) => watchSourceItems(element, getterCtx))
      return keysToItems(canonical(body, getterCtx), body)
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
      return watchSourceItems(last.expression, getterCtx)
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
    const ctx = { aliases: [] }
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
  const ctx = { ...callSites, bindings: new Map(), aliases: [] }
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
        if (mapped) {
          // A key handed as a getter by every call site is a function here; one that
          // some call site passes as a ref is still the property's value.
          const getterOf = callSites.scopePropGetter?.get(source)
          const getterKeys = new Set()
          for (const key of mapped) if (getterOf?.get(key)) getterKeys.add(key)
          ctx.bindings.set(element.name.text, { keys: mapped, getterKeys })
        }
        // A call site that hands over the whole props object destructures the prop
        // itself, so the local name is the prop path.
        else if (callSites.scopeWholeProps) {
          ctx.bindings.set(element.name.text, { keys: new Set([source]), getterKeys: new Set() })
        }
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

// Call sites are read per caller file: how the composable reads its parameter depends
// on what *that* component hands over, so one component's call site must not decide
// for another. The returned map is file -> the scope that file's calls build.
function callSiteMappings(vueFiles, fnName, readHead) {
  const byFile = new Map()
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
    const scopeProps = new Map()
    // Property -> its keys and whether every call in this file hands each one over as
    // a function (a getter). A key this file passes as a ref is read as written here.
    const scopePropGetter = new Map()
    const scopeValue = new Map()
    let scopeWholeProps = false
    let called = false
    const add = (key, keys, getter) => {
      if (!scopeProps.has(key)) {
        scopeProps.set(key, new Set())
        scopePropGetter.set(key, new Map())
      }
      for (const value of keys) {
        scopeProps.get(key).add(value)
        // `!getter` counts, it is not left unknown: a plain property hands over
        // whatever it holds, so one such call keeps the key readable.
        const seen = scopePropGetter.get(key).get(value)
        scopePropGetter.get(key).set(value, Boolean(getter) && seen !== false)
      }
    }
    for (const block of blocks) {
      const visit = (node) => {
        const callee = ts.isCallExpression(node) ? unwrap(node.expression) : null
        if (callee && ts.isIdentifier(callee) && callee.text === fnName && node.arguments.length) {
          called = true
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
    if (called) byFile.set(file, { scopeProps, scopePropGetter, scopeWholeProps, scopeValue })
  }
  return byFile
}

// A component that no longer calls the composable has no call site of its own, so
// there is no reading to judge it by. Fall back to what the remaining callers hand
// over, permissively: a key any caller passes as a ref is readable everywhere.
function callerMapping(byFile, caller) {
  const own = byFile.get(caller)
  if (own || !byFile.size) return own ?? {}
  const merged = { scopeProps: new Map(), scopePropGetter: new Map(), scopeValue: new Map(), scopeWholeProps: false }
  for (const scope of byFile.values()) {
    for (const [name, keys] of scope.scopeProps ?? []) {
      if (!merged.scopeProps.has(name)) {
        merged.scopeProps.set(name, new Set())
        merged.scopePropGetter.set(name, new Map())
      }
      for (const key of keys) {
        merged.scopeProps.get(name).add(key)
        const getter = scope.scopePropGetter?.get(name)?.get(key)
        const seen = merged.scopePropGetter.get(name).get(key)
        merged.scopePropGetter.get(name).set(key, (seen ?? true) && Boolean(getter))
      }
    }
    if (scope.scopeWholeProps) merged.scopeWholeProps = true
    for (const [key, asRef] of scope.scopeValue ?? [])
      merged.scopeValue.set(key, Boolean(merged.scopeValue.get(key)) || asRef)
  }
  return merged
}

function analyzeComposable(file, source, caller, mappingFor) {
  const sourceFile = parseModule(source, file)
  const items = []
  const scopes = exportedFunctionRanges(sourceFile).map((found) => ({
    start: found.node.getStart(sourceFile),
    end: found.node.getEnd(),
    ctx: scopeContext(found, callerMapping(mappingFor(found.name), caller)),
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
  const vueFiles = listVueFiles(repo, target, options.staged)
  const mappingCache = new Map()
  const mappingFor = (fnName) => {
    if (!mappingCache.has(fnName)) mappingCache.set(fnName, callSiteMappings(vueFiles, fnName, readHead))
    return mappingCache.get(fnName)
  }
  const composableItems = new Map()
  const composableFor = (caller) => {
    const out = new Map()
    for (const file of targets) {
      const key = `${caller}\0${file}`
      if (!composableItems.has(key))
        composableItems.set(key, analyzeComposable(file, readHead(file), caller, mappingFor))
      out.set(file, composableItems.get(key))
    }
    return out
  }

  const headAvailable = new Map()
  const addAvailable = (available, file, items) => {
    for (const item of items) if (!available.has(item.key)) available.set(item.key, { file, line: item.line })
  }
  for (const [file, items] of headItems) addAvailable(headAvailable, file, items)

  // The composable is read the way the component whose watch is being judged calls
  // it: a key one caller hands over as a getter does not exist for a caller that
  // hands over the ref.
  const availableCache = new Map()
  const availableFor = (caller) => {
    if (!availableCache.has(caller)) {
      const available = new Map(headAvailable)
      for (const [file, items] of composableFor(caller)) addAvailable(available, file, items)
      availableCache.set(caller, available)
    }
    return availableCache.get(caller)
  }

  const findings = []
  let checked = 0
  for (const [file, items] of baseItems) {
    for (const item of items) {
      checked++
      if (isEquivalent(availableFor(file), item.key)) continue
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
