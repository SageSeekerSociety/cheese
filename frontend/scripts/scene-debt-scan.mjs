import path from 'node:path'
import { pathToFileURL } from 'node:url'

import ts from 'typescript'

// The Python gate supplies script blocks, not templates. TypeScript's AST keeps
// examples in strings/comments out of both the import and call inventories.
export function scanScript(file, code) {
  const source = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true)
  if (source.parseDiagnostics.length) {
    throw new Error(`${file}: ${ts.flattenDiagnosticMessageText(source.parseDiagnostics[0].messageText, '\n')}`)
  }
  const imports = {}
  const routes = new Set(['useRoute'])
  const namespaces = new Set()
  const specs = new Set()
  for (const node of source.statements) {
    if (!ts.isImportDeclaration(node) || !ts.isStringLiteral(node.moduleSpecifier)) continue
    const spec = node.moduleSpecifier.text
    const clause = node.importClause
    specs.add(spec) // Type-only imports still prolong the old network API.
    if (!clause || clause.isTypeOnly) continue
    if (clause.name) imports[clause.name.text] = spec
    const bindings = clause.namedBindings
    if (bindings && ts.isNamedImports(bindings)) {
      for (const element of bindings.elements) {
        if (element.isTypeOnly) continue
        imports[element.name.text] = spec
        if (spec === 'vue-router' && (element.propertyName ?? element.name).text === 'useRoute') {
          routes.add(element.name.text)
        }
      }
    } else if (bindings && ts.isNamespaceImport(bindings) && spec === 'vue-router') {
      namespaces.add(bindings.name.text)
    }
  }
  let useRoute = false
  function visit(node) {
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      specs.add(node.moduleSpecifier.text)
    }
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const expression = node.moduleReference.expression
      if (expression && ts.isStringLiteral(expression)) specs.add(expression.text)
    }
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal)) {
      specs.add(node.argument.literal.text)
    }
    if (ts.isCallExpression(node)) {
      const expression = node.expression
      if (ts.isIdentifier(expression) && routes.has(expression.text)) useRoute = true
      if (
        ts.isPropertyAccessExpression(expression) &&
        ts.isIdentifier(expression.expression) &&
        namespaces.has(expression.expression.text) &&
        expression.name.text === 'useRoute'
      )
        useRoute = true
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
  return { imports, useRoute, network }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let input = ''
    for await (const chunk of process.stdin) input += chunk
    const files = JSON.parse(input)
    const result = Object.fromEntries(files.map(([file, code]) => [file, scanScript(file, code)]))
    process.stdout.write(JSON.stringify(result))
  } catch (error) {
    process.stderr.write(`cannot scan frontend scripts: ${error.message}\n`)
    process.exitCode = 2
  }
}
