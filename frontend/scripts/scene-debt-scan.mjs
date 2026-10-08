import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { parse } from 'vue/compiler-sfc'
import ts from 'typescript'

function syntax(file, code) {
  const source = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true)
  if (source.parseDiagnostics.length) {
    throw new Error(`${file}: ${ts.flattenDiagnosticMessageText(source.parseDiagnostics[0].messageText, '\n')}`)
  }
  return source
}

function unwrap(node) {
  while (
    ts.isParenthesizedExpression(node) ||
    ts.isAsExpression(node) ||
    ts.isTypeAssertionExpression(node) ||
    ts.isNonNullExpression(node) ||
    ts.isSatisfiesExpression(node)
  )
    node = node.expression
  return node
}

function member(node) {
  node = unwrap(node)
  if (ts.isPropertyAccessExpression(node)) return [unwrap(node.expression), node.name.text]
  if (
    ts.isElementAccessExpression(node) &&
    node.argumentExpression &&
    ts.isStringLiteralLike(node.argumentExpression)
  ) {
    return [unwrap(node.expression), node.argumentExpression.text]
  }
  return []
}

function templates(blocks) {
  const dynamic = new Set()
  const expressions = []
  function scoped(expression, bindings) {
    return bindings.reduceRight((body, binding) => `;(${binding} => { ${body} });`, expression)
  }
  function visit(node, bindings = []) {
    if (node.type === 5 && node.content.content.trim()) expressions.push(scoped(`(${node.content.content});`, bindings))
    if (node.type === 1) {
      bindings = [...bindings]
      for (const prop of node.props) {
        if (prop.type !== 7 || !prop.exp?.content.trim()) continue
        if (prop.name === 'for') {
          const loop = prop.exp.content.match(/^(.*?)\s+(?:in|of)\s+([\s\S]*)$/)
          if (!loop) throw new Error('cannot parse template loop')
          expressions.push(scoped(`(${loop[2]});`, bindings))
          bindings.push(`(${loop[1].replace(/^\(|\)$/g, '')})`)
        } else if (prop.name === 'slot') bindings.push(`(${prop.exp.content})`)
      }
      for (const prop of node.props) {
        if (prop.type !== 7 || !prop.exp?.content.trim() || prop.name === 'for' || prop.name === 'slot') continue
        if (node.tag.toLowerCase() === 'component' && prop.name === 'bind' && prop.arg?.content === 'is') {
          dynamic.add(expressions.length)
        }
        expressions.push(scoped(prop.name === 'on' ? prop.exp.content : `(${prop.exp.content});`, bindings))
      }
    }
    for (const child of node.children ?? []) visit(child, bindings)
  }
  for (const block of blocks) {
    const result = parse(`<template>${block}</template>`)
    if (result.errors.length) throw new Error(String(result.errors[0]))
    if (result.descriptor.template) visit(result.descriptor.template.ast)
  }
  return { dynamic, expressions }
}

// TypeScript's binder distinguishes imported route readers from shadowing
// parameters. Vue parses SFC boundaries and template expressions first.
export function scanScript(file, code, blocks = []) {
  const template = templates(blocks)
  let combined = `${code}\n`
  const dynamic = []
  for (const [index, expression] of template.expressions.entries()) {
    const start = combined.length
    combined += `;(() => { ${expression} })();\n`
    if (template.dynamic.has(index)) dynamic.push([start, combined.length])
  }
  const source = syntax(file, combined)
  const host = {
    getSourceFile: (name) => (name === file ? source : undefined),
    getDefaultLibFileName: () => '',
    writeFile() {},
    getCurrentDirectory: () => '',
    getDirectories: () => [],
    fileExists: (name) => name === file,
    readFile: (name) => (name === file ? source.text : undefined),
    getCanonicalFileName: (name) => name,
    useCaseSensitiveFileNames: () => true,
    getNewLine: () => '\n',
  }
  const checker = ts
    .createProgram([file], { noLib: true, noResolve: true, allowNonTsExtensions: true }, host)
    .getTypeChecker()
  const imports = {}
  const components = {}
  const routes = new Set()
  const namespaces = new Set()
  const asyncHelpers = new Set()
  const vueNamespaces = new Set()
  const specs = new Set()
  const declarations = new Map()
  const scriptBindings = new Map()
  for (const node of source.statements) {
    if (ts.isVariableStatement(node)) {
      for (const declaration of node.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name) && declaration.initializer) {
          declarations.set(declaration.name.text, declaration.initializer)
          scriptBindings.set(declaration.name.text, checker.getSymbolAtLocation(declaration.name))
        }
      }
    }
    if (!ts.isImportDeclaration(node) || !ts.isStringLiteral(node.moduleSpecifier)) continue
    const spec = node.moduleSpecifier.text
    const clause = node.importClause
    specs.add(spec) // Types still prolong the old network API.
    if (!clause || clause.isTypeOnly) continue
    if (clause.name) {
      imports[clause.name.text] = spec
      scriptBindings.set(clause.name.text, checker.getSymbolAtLocation(clause.name))
    }
    const bindings = clause.namedBindings
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) {
        if (element.isTypeOnly) continue
        imports[element.name.text] = spec
        const imported = (element.propertyName ?? element.name).text
        const symbol = checker.getSymbolAtLocation(element.name)
        scriptBindings.set(element.name.text, symbol)
        if (spec === 'vue-router' && imported === 'useRoute') routes.add(symbol)
        if (spec === 'vue' && imported === 'defineAsyncComponent') asyncHelpers.add(symbol)
      }
    } else if (bindings && ts.isNamespaceImport(bindings)) {
      const symbol = checker.getSymbolAtLocation(bindings.name)
      if (spec === 'vue-router') namespaces.add(symbol)
      if (spec === 'vue') vueNamespaces.add(symbol)
    }
  }
  for (const [local, spec] of Object.entries(imports)) components[local] = [spec]
  function matches(node, named, namespace, name) {
    node = unwrap(node)
    if (ts.isIdentifier(node)) {
      const symbol = checker.getSymbolAtLocation(node)
      return named.has(symbol) || (!symbol && node.text === name)
    }
    const [base, property] = member(node)
    return property === name && base && namespace.has(checker.getSymbolAtLocation(base))
  }
  for (const [local, initializer] of declarations) {
    const call = unwrap(initializer)
    if (!ts.isCallExpression(call) || !matches(call.expression, asyncHelpers, vueNamespaces, 'defineAsyncComponent'))
      continue
    const targets = new Set()
    function loader(node) {
      if (
        ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword &&
        node.arguments[0] &&
        ts.isStringLiteralLike(node.arguments[0])
      )
        targets.add(node.arguments[0].text)
      ts.forEachChild(node, loader)
    }
    loader(call)
    if (targets.size) components[local] = [...targets]
  }
  const rendered = new Set()
  function references(node, seen = new Set()) {
    if (ts.isIdentifier(node)) {
      const local = node.text
      if (checker.getSymbolAtLocation(node) !== scriptBindings.get(local)) return
      if (Object.hasOwn(components, local)) {
        rendered.add(local)
        return
      }
      if (!seen.has(local) && declarations.has(local)) {
        seen.add(local)
        references(declarations.get(local), seen)
      }
    }
    if (ts.isPropertyAccessExpression(node)) references(node.expression, seen)
    else if (ts.isPropertyAssignment(node)) references(node.initializer, seen)
    else ts.forEachChild(node, (child) => references(child, seen))
  }
  // Keep dynamic selectors in the same bound template scope as route calls.
  for (const statement of source.statements) {
    if (dynamic.some(([start, end]) => statement.getStart(source) >= start && statement.end <= end))
      references(statement)
  }
  let useRoute = false
  function visit(node) {
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier))
      specs.add(node.moduleSpecifier.text)
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const expression = node.moduleReference.expression
      if (expression && ts.isStringLiteral(expression)) specs.add(expression.text)
    }
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal))
      specs.add(node.argument.literal.text)
    if (ts.isCallExpression(node)) {
      const expression = unwrap(node.expression)
      if (matches(expression, routes, namespaces, 'useRoute')) useRoute = true
      if (
        (expression.kind === ts.SyntaxKind.ImportKeyword ||
          (ts.isIdentifier(expression) && expression.text === 'require')) &&
        node.arguments[0] &&
        ts.isStringLiteralLike(node.arguments[0])
      )
        specs.add(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  const network = [...specs].some((spec) => {
    let target
    if (spec.startsWith('@/')) target = path.posix.normalize(`src/${spec.slice(2)}`)
    else if (spec.startsWith('.')) target = path.posix.normalize(path.posix.join(path.posix.dirname(file), spec))
    else if (spec.startsWith('/src/')) target = path.posix.normalize(spec.slice(1))
    else return false
    return target === 'src/network' || target.startsWith('src/network/')
  })
  return { imports, components, rendered: [...rendered], useRoute, network }
}

export function scanFile(file, text) {
  if (!file.endsWith('.vue')) return scanScript(file, text)
  const result = parse(text, { filename: file })
  if (result.errors.length) throw new Error(`${file}: ${String(result.errors[0])}`)
  const descriptor = result.descriptor
  const scripts = [descriptor.script, descriptor.scriptSetup].filter(Boolean)
  const extension = scripts.some((script) => script.lang === 'tsx' || script.lang === 'jsx') ? '.tsx' : '.ts'
  const code = scripts
    .map((script) => (script.src ? `import ${JSON.stringify(script.src)}` : script.content))
    .join('\n')
  return scanScript(file + extension, code, descriptor.template ? [descriptor.template.content] : [])
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let input = ''
    for await (const chunk of process.stdin) input += chunk
    const files = JSON.parse(input)
    const result = Object.fromEntries(files.map(([file, text]) => [file, scanFile(file, text)]))
    process.stdout.write(JSON.stringify(result))
  } catch (error) {
    process.stderr.write(`cannot scan frontend scripts: ${error.message}\n`)
    process.exitCode = 2
  }
}
