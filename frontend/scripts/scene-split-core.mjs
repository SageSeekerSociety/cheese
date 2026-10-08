// The pure half of `scene:split` (scripts/scene-split.mjs): read a route page,
// work out which of its setup bindings the template needs and how, and produce
// the sibling `<Page>View.vue` plus the rewritten page. Nothing here writes to
// disk; the CLI does that. What this module cannot map mechanically it reports
// (`plan.unsafe`) instead of guessing — see the CLI docstring for the contract.

import fs from 'node:fs'
import path from 'node:path'

import { babelParse, compileScript, MagicString, parse as parseSfc, walkIdentifiers } from 'vue/compiler-sfc'
import ts from 'typescript'

// ---------------------------------------------------------------------------
// Small helpers

/** `saveDraft` -> `save-draft`. */
export function kebab(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1-$2')
    .toLowerCase()
}

/**
 * The event a handler function becomes: the kebab-case of its name, with a
 * leading `on`/`handle` dropped (`onAvatarPicked` -> `avatar-picked`), since a
 * listener called `@on-avatar-picked` reads as a typo.
 */
export function eventName(fn) {
  const stripped = fn.replace(/^(on|handle)(?=[A-Z])/, '')
  return kebab(stripped || fn)
}

function pascal(tag) {
  return tag.replace(/(^|-)(\w)/g, (_, __, c) => c.toUpperCase())
}

function camel(tag) {
  return tag.replace(/-(\w)/g, (_, c) => c.toUpperCase())
}

function lineOf(source, offset) {
  let line = 1
  for (let i = 0; i < offset && i < source.length; i++) if (source.charCodeAt(i) === 10) line++
  return line
}

// Identifiers a template may use without a binding (Vue's allow-list).
const TEMPLATE_GLOBALS = new Set(
  (
    'Infinity,undefined,NaN,isFinite,isNaN,parseFloat,parseInt,decodeURI,decodeURIComponent,' +
    'encodeURI,encodeURIComponent,Math,Number,Date,Array,Object,Boolean,String,RegExp,Map,Set,' +
    'JSON,Intl,BigInt,console,Error,Symbol,true,false,null,this'
  ).split(',')
)

// Instance properties a template reaches that the split cannot carry over.
const UNSAFE_INSTANCE = {
  $slots: 'reads $slots: the view has its own slots, the page passes none',
  $attrs: 'reads $attrs: fallthrough attributes land on the view, not the page',
  $route: 'reads $route in the template: the view must not know the route',
  $router: 'uses $router in the template: navigation belongs to the page',
  $refs: 'reads $refs',
  $parent: 'reads $parent',
  $root: 'reads $root',
  $emit: 'calls $emit: the page itself emits, re-wire by hand',
  $props: 'reads $props',
  $el: 'reads $el',
}

const RESERVED_PROPS = new Set(['key', 'ref', 'class', 'style', 'is', 'slot', 'emit'])

// Where a template-used import makes the view stop being grade A.
const IMPURE_SOURCE = /^(@\/api|@\/network|@\/services|@\/stores|vue-router$|\.\.?\/api(\/|$))/

const REF_TYPES = new Set(['Ref', 'ShallowRef', 'ComputedRef', 'WritableComputedRef', 'ModelRef'])
const WRITABLE_REF_TYPES = new Set(['Ref', 'ShallowRef', 'WritableComputedRef', 'ModelRef'])

// ---------------------------------------------------------------------------
// Script analysis (babel AST from compileScript)

/** Top-level declarations of `<script setup>`: name -> { kind, node, init, ... }. */
function setupDeclarations(ast) {
  const decls = new Map()
  for (const stmt of ast.body) {
    if (stmt.type === 'FunctionDeclaration' && stmt.id) {
      decls.set(stmt.id.name, { kind: 'function', stmt, params: stmt.params })
    } else if (stmt.type === 'VariableDeclaration') {
      for (const d of stmt.declarations) {
        const init = d.init
        if (d.id.type === 'Identifier') {
          const isFn = init && (init.type === 'ArrowFunctionExpression' || init.type === 'FunctionExpression')
          decls.set(d.id.name, {
            kind: isFn ? 'function' : stmt.kind === 'const' ? 'const' : 'let',
            stmt,
            declarator: d,
            init,
            params: isFn ? init.params : null,
          })
        } else if (d.id.type === 'ObjectPattern' || d.id.type === 'ArrayPattern') {
          for (const name of patternNames(d.id)) {
            decls.set(name, {
              kind: stmt.kind === 'const' ? 'const' : 'let',
              stmt,
              declarator: d,
              init,
              destructured: true,
            })
          }
        }
      }
    }
  }
  return decls
}

function patternNames(p, out = []) {
  if (!p) return out
  switch (p.type) {
    case 'Identifier':
      out.push(p.name)
      break
    case 'ObjectPattern':
      for (const prop of p.properties) patternNames(prop.type === 'RestElement' ? prop.argument : prop.value, out)
      break
    case 'ArrayPattern':
      for (const el of p.elements) patternNames(el, out)
      break
    case 'AssignmentPattern':
      patternNames(p.left, out)
      break
    case 'RestElement':
      patternNames(p.argument, out)
      break
    case 'TSParameterProperty':
      patternNames(p.parameter, out)
      break
  }
  return out
}

function calleeName(init) {
  if (!init) return null
  let e = init
  if (e.type === 'AwaitExpression') e = e.argument
  if (e.type !== 'CallExpression') return null
  return e.callee.type === 'Identifier' ? e.callee.name : null
}

/** Every identifier name the script reads, outside import declarations, property keys and the names a declaration binds. */
function scriptReferences(ast) {
  const used = new Set()
  const isFunction = (n) =>
    n.type === 'FunctionDeclaration' ||
    n.type === 'FunctionExpression' ||
    n.type === 'ArrowFunctionExpression' ||
    n.type === 'ObjectMethod'
  const visit = (node, parent, key, binding) => {
    if (!node || typeof node.type !== 'string') return
    if (node.type === 'ImportDeclaration') return
    if (node.type === 'Identifier') {
      const isKey =
        parent &&
        ((parent.type === 'MemberExpression' && key === 'property' && !parent.computed) ||
          (parent.type === 'OptionalMemberExpression' && key === 'property' && !parent.computed) ||
          ((parent.type === 'ObjectProperty' || parent.type === 'ObjectMethod') &&
            key === 'key' &&
            !parent.computed &&
            !parent.shorthand) ||
          (parent.type === 'TSPropertySignature' && key === 'key'))
      if (!isKey && !binding) used.add(node.name)
    }
    for (const k of Object.keys(node)) {
      if (k === 'loc' || k === 'start' || k === 'end' || k === 'leadingComments' || k === 'trailingComments') continue
      // A declaration's own name is a binding, not a read: `const route = useRoute()`
      // reads `useRoute`, not `route`, and `const { t } = useI18n()` reads `useI18n`.
      // Inside a binding, only the default of an assignment is an expression again.
      let childBinding = binding
      if (k === 'id' && (node.type === 'VariableDeclarator' || isFunction(node) || node.type === 'ClassDeclaration'))
        childBinding = true
      else if (k === 'params' && isFunction(node)) childBinding = true
      else if (k === 'param' && node.type === 'CatchClause') childBinding = true
      else if (k === 'init' && node.type === 'VariableDeclarator') childBinding = false
      else if (k === 'right' && node.type === 'AssignmentPattern') childBinding = false
      const v = node[k]
      if (Array.isArray(v)) v.forEach((c) => c && typeof c === 'object' && visit(c, node, k, childBinding))
      else if (v && typeof v === 'object' && typeof v.type === 'string') visit(v, node, k, childBinding)
    }
  }
  visit(ast, null, null, false)
  return used
}

// ---------------------------------------------------------------------------
// Template analysis

const ELEMENT = 1
const INTERPOLATION = 5
const ATTRIBUTE = 6
const DIRECTIVE = 7
const TAG_COMPONENT = 1

function parseExpressionAt(content) {
  // Wrapped in parentheses so object literals and sequences parse; offsets
  // returned below are relative to `content`.
  const program = babelParse(`(${content}\n)`, { plugins: ['typescript'] })
  const stmt = program.program.body[0]
  if (!stmt || stmt.type !== 'ExpressionStatement' || program.program.body.length !== 1)
    throw new Error('not one expression')
  return { root: stmt.expression, shift: 1 }
}

function parseStatements(content) {
  const program = babelParse(content, { plugins: ['typescript'] })
  return { root: program.program, shift: 0 }
}

function isSimplePath(node) {
  if (node.type === 'Identifier') return true
  if (node.type === 'MemberExpression' && !node.computed) return isSimplePath(node.object)
  return false
}

function rootIdentifier(node) {
  let n = node
  while (n && (n.type === 'MemberExpression' || n.type === 'OptionalMemberExpression')) n = n.object
  return n && n.type === 'Identifier' ? n : null
}

/**
 * Walks the raw template AST and records, for every free identifier, how it
 * is used. `ctx` is what the expression sits in:
 *   'expr'    an interpolation or a bound attribute
 *   'handler' a v-on value
 *   'model'   a v-model value
 */
function scanTemplate(templateAst) {
  const uses = [] // { name, how, offset, exprIndex }
  const exprs = [] // { content, base, ctx, parsed, eventArg, node }
  const tags = [] // { tag, offset }
  const directives = [] // { name, offset }
  const templateRefs = [] // { name, offset }
  const dynamicComponents = [] // { offset }
  const slotOutlets = []

  function scopeNamesOfParams(content) {
    try {
      const { root } = parseExpressionAt(`(${content}) => 0`)
      return root.params.flatMap((p) => patternNames(p))
    } catch {
      return []
    }
  }

  function record(content, base, ctx, scope, meta = {}) {
    let parsed
    let kind = 'expr'
    try {
      if (ctx === 'handler') {
        try {
          parsed = parseExpressionAt(content)
          if (isSimplePath(parsed.root)) kind = 'bare'
          else if (parsed.root.type === 'ArrowFunctionExpression' || parsed.root.type === 'FunctionExpression')
            kind = 'fn'
          else kind = 'inline'
        } catch {
          parsed = parseStatements(content)
          kind = 'inline'
        }
      } else {
        parsed = parseExpressionAt(content)
      }
    } catch (err) {
      uses.push({ name: null, how: 'unparsed', offset: base, detail: String(err.message || err) })
      return
    }
    const index = exprs.length
    exprs.push({ content, base, ctx, kind, parsed, ...meta })
    const known = new Set(scope)
    if (ctx === 'handler') known.add('$event')
    walkIdentifiers(
      parsed.root,
      (id, parent, parentStack, isReference, isLocal) => {
        if (isLocal || known.has(id.name)) return
        const at = base + id.start - parsed.shift
        let how = 'read'
        if (
          parent &&
          (parent.type === 'CallExpression' || parent.type === 'OptionalCallExpression') &&
          parent.callee === id
        ) {
          how = 'call'
        } else if (parent && parent.type === 'AssignmentExpression' && parent.left === id) {
          how = 'assign'
        } else if (parent && parent.type === 'UpdateExpression') {
          how = 'assign'
        } else if (
          parent &&
          (parent.type === 'MemberExpression' || parent.type === 'OptionalMemberExpression') &&
          parent.object === id
        ) {
          // Walk to the top of the member chain and see if it is written.
          let top = parent
          let i = parentStack.length - 2
          while (
            i >= 0 &&
            (parentStack[i].type === 'MemberExpression' || parentStack[i].type === 'OptionalMemberExpression') &&
            parentStack[i].object === top
          ) {
            top = parentStack[i]
            i--
          }
          const holder = parentStack[i]
          if (
            holder &&
            ((holder.type === 'AssignmentExpression' && holder.left === top) || holder.type === 'UpdateExpression')
          )
            how = 'mutate'
        }
        if (ctx === 'model')
          how = parsed.root === id ? 'assign' : how === 'read' && rootIdentifier(parsed.root) === id ? 'mutate' : how
        if (ctx === 'handler' && kind === 'bare' && parsed.root === id) how = 'bare'
        uses.push({ name: id.name, how, ctx, offset: at, exprIndex: index, node: id, parent })
      },
      false,
      [],
      new Set()
    )
  }

  function visit(node, scope) {
    if (node.type === INTERPOLATION) {
      record(node.content.content, node.content.loc.start.offset, 'expr', scope)
      return
    }
    if (node.type !== ELEMENT) {
      for (const c of node.children || []) visit(c, scope)
      return
    }
    let inner = scope
    const forDir = node.props.find((p) => p.type === DIRECTIVE && p.name === 'for')
    if (forDir && forDir.exp) {
      const m = /^\s*([\s\S]*?)\s+(?:in|of)\s+([\s\S]*)$/.exec(forDir.exp.content)
      if (m) {
        const lhs = m[1].trim().replace(/^\(([\s\S]*)\)$/, '$1')
        const srcStart = forDir.exp.loc.start.offset + forDir.exp.content.indexOf(m[2], m[1].length)
        record(m[2], srcStart, 'expr', scope)
        inner = [...scope, ...scopeNamesOfParams(lhs)]
      }
    }
    if (node.tagType === TAG_COMPONENT) tags.push({ tag: node.tag, offset: node.loc.start.offset })
    if (node.tag === 'component') dynamicComponents.push({ offset: node.loc.start.offset })
    if (node.tag === 'slot') slotOutlets.push({ offset: node.loc.start.offset })
    let childScope = inner
    for (const p of node.props) {
      if (p.type === ATTRIBUTE) {
        if (p.name === 'ref' && p.value) templateRefs.push({ name: p.value.content, offset: p.loc.start.offset })
        continue
      }
      if (p.type !== DIRECTIVE || p === forDir) continue
      if (
        ![
          'bind',
          'on',
          'model',
          'if',
          'else-if',
          'show',
          'html',
          'text',
          'slot',
          'memo',
          'for',
          'else',
          'once',
          'pre',
          'cloak',
        ].includes(p.name)
      ) {
        directives.push({ name: p.name, offset: p.loc.start.offset })
      }
      if (p.arg && !p.arg.isStatic) record(p.arg.content, p.arg.loc.start.offset, 'expr', inner)
      if (p.name === 'bind' && p.arg && p.arg.isStatic && p.arg.content === 'ref') {
        templateRefs.push({ name: p.exp ? p.exp.content : '', offset: p.loc.start.offset })
      }
      if (p.name === 'slot') {
        if (p.exp) childScope = [...childScope, ...scopeNamesOfParams(p.exp.content)]
        continue
      }
      if (!p.exp) continue
      const ctx = p.name === 'on' ? 'handler' : p.name === 'model' ? 'model' : 'expr'
      record(p.exp.content, p.exp.loc.start.offset, ctx, inner, {
        eventArg: p.name === 'on' && p.arg ? p.arg.content : null,
        modelArg: p.name === 'model' ? (p.arg ? p.arg.content : 'modelValue') : null,
        tag: node.tag,
      })
    }
    for (const c of node.children || []) visit(c, childScope)
  }

  visit(templateAst, [])
  return { uses, exprs, tags, directives, templateRefs, dynamicComponents, slotOutlets }
}

// ---------------------------------------------------------------------------
// Types (TypeScript compiler API over the raw `<script setup>` block)

let lastProgram = null

function loadTsOptions(tsconfigPath) {
  const read = ts.readConfigFile(tsconfigPath, ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, path.dirname(tsconfigPath))
  const options = {
    ...parsed.options,
    noEmit: true,
    skipLibCheck: true,
    noUnusedLocals: false,
    noUnusedParameters: false,
  }
  // tsconfig.app.json extends @vue/tsconfig but the `@/` paths live in the root tsconfig.
  if (!options.paths) {
    const root = path.join(path.dirname(tsconfigPath), 'tsconfig.json')
    const rootRead = ts.readConfigFile(root, ts.sys.readFile)
    if (rootRead.config?.compilerOptions?.paths) {
      options.paths = rootRead.config.compilerOptions.paths
      options.baseUrl = path.dirname(root)
    }
  }
  delete options.tsBuildInfoFile
  delete options.composite
  delete options.incremental
  return options
}

function packageSpecifier(file) {
  const i = file.lastIndexOf('/node_modules/')
  if (i < 0) return null
  const rest = file.slice(i + '/node_modules/'.length).split('/')
  let name = rest[0].startsWith('@') ? `${rest[0]}/${rest[1]}` : rest[0]
  if (/^@vue\/(runtime-core|runtime-dom|reactivity|shared)$/.test(name)) name = 'vue'
  return name
}

/**
 * Resolves the TS type of every name in `names` declared at the top level of
 * the page's `<script setup>`. Returns name -> info:
 *   { type, isRef, writable, isFunction, params: [{ name, type, optional, rest }],
 *     imports: [{ name, from }], localDecls: [text], todo }
 */
export function resolveTypes({ pagePath, scriptContent, names, srcDir, frontendDir, tsconfig, pageImports }) {
  const result = new Map()
  const fallback = (why) => ({
    type: 'unknown',
    isRef: false,
    writable: false,
    isFunction: false,
    params: null,
    imports: [],
    localDecls: [],
    todo: why,
  })
  let program
  let checker
  let sf
  const virtual = `${pagePath}.scene-split.ts`
  try {
    const options = loadTsOptions(tsconfig)
    const host = ts.createCompilerHost(options, true)
    const origGet = host.getSourceFile.bind(host)
    const origExists = host.fileExists.bind(host)
    const origRead = host.readFile.bind(host)
    host.getSourceFile = (f, lang, ...rest) =>
      path.resolve(f) === virtual
        ? ts.createSourceFile(f, scriptContent, lang, true, ts.ScriptKind.TS)
        : origGet(f, lang, ...rest)
    host.fileExists = (f) => path.resolve(f) === virtual || origExists(f)
    host.readFile = (f) => (path.resolve(f) === virtual ? scriptContent : origRead(f))
    const roots = [virtual]
    for (const d of ['vite-env.d.ts', 'shims-svg.d.ts']) {
      const p = path.join(frontendDir, 'src', d)
      if (fs.existsSync(p)) roots.push(p)
    }
    program = ts.createProgram({ rootNames: roots, options, host, oldProgram: lastProgram ?? undefined })
    lastProgram = program
    checker = program.getTypeChecker()
    sf = program.getSourceFile(virtual)
  } catch (err) {
    for (const n of names) result.set(n, fallback(`type checker failed: ${err.message}`))
    return result
  }

  // name -> declaring identifier node
  const idents = new Map()
  const localTypeDecls = new Map()
  const collectBinding = (b) => {
    if (ts.isIdentifier(b)) idents.set(b.text, b)
    else if (ts.isObjectBindingPattern(b) || ts.isArrayBindingPattern(b)) {
      for (const el of b.elements) if (!ts.isOmittedExpression(el)) collectBinding(el.name)
    }
  }
  for (const stmt of sf.statements) {
    if (ts.isFunctionDeclaration(stmt) && stmt.name) idents.set(stmt.name.text, stmt.name)
    else if (ts.isVariableStatement(stmt)) for (const d of stmt.declarationList.declarations) collectBinding(d.name)
    else if (ts.isInterfaceDeclaration(stmt) || ts.isTypeAliasDeclaration(stmt) || ts.isEnumDeclaration(stmt)) {
      localTypeDecls.set(stmt.name.text, stmt.getText(sf))
    }
  }
  const flags = ts.TypeFormatFlags.NoTruncation | ts.TypeFormatFlags.UseAliasDefinedOutsideCurrentScope
  const print = (t) => checker.typeToString(t, sf, flags)

  const refName = (t) => {
    const sym = t.aliasSymbol ?? t.getSymbol()
    if (!sym || !REF_TYPES.has(sym.name)) return null
    const decl = sym.declarations?.[0]
    if (!decl || !/node_modules\/(\.pnpm\/.*\/)?@vue\//.test(decl.getSourceFile().fileName)) return null
    return sym.name
  }

  function namedSymbols(type, node) {
    const out = new Set()
    const seen = new Set()
    const walk = (t, depth) => {
      if (!t || depth > 8 || seen.has(t)) return
      seen.add(t)
      if (t.aliasSymbol) {
        out.add(t.aliasSymbol)
        for (const a of t.aliasTypeArguments ?? []) walk(a, depth + 1)
        return
      }
      if (t.flags & ts.TypeFlags.EnumLiteral && t.symbol) {
        out.add(t.symbol.parent ?? t.symbol)
        if (t.isUnion()) return
      }
      if (t.isUnionOrIntersection()) {
        for (const x of t.types) walk(x, depth + 1)
        return
      }
      if (t.flags & ts.TypeFlags.Object) {
        const of = t.objectFlags
        if (of & ts.ObjectFlags.Reference) {
          if (t.target?.symbol) out.add(t.target.symbol)
          for (const a of checker.getTypeArguments(t)) walk(a, depth + 1)
          return
        }
        if (t.symbol && t.symbol.flags & (ts.SymbolFlags.Interface | ts.SymbolFlags.Class | ts.SymbolFlags.Enum)) {
          out.add(t.symbol)
          return
        }
        for (const p of t.getProperties()) walk(checker.getTypeOfSymbolAtLocation(p, node), depth + 1)
        for (const s of t.getCallSignatures()) {
          for (const p of s.parameters) walk(checker.getTypeOfSymbolAtLocation(p, node), depth + 1)
          walk(s.getReturnType(), depth + 1)
        }
      }
    }
    walk(type, 0)
    return out
  }

  function importsFor(type, text, node) {
    const imports = []
    const localDecls = []
    let todo = null
    for (const sym of namedSymbols(type, node)) {
      const name = sym.name
      if (!new RegExp(`(^|[^\\w$.])${name.replace(/\$/g, '\\$')}([^\\w$]|$)`).test(text)) continue
      const decl = sym.declarations?.[0]
      if (!decl) continue
      const file = decl.getSourceFile()
      if (path.resolve(file.fileName) === virtual) {
        if (localTypeDecls.has(name)) localDecls.push(localTypeDecls.get(name))
        else if (pageImports.has(name))
          imports.push({ name: pageImports.get(name).imported, local: name, from: pageImports.get(name).source })
        else todo = `type ${name} is declared in the page in a shape the split cannot copy`
        continue
      }
      if (!ts.isExternalModule(file)) continue // a global script (lib.dom, vite/client): nothing to import
      let p = decl.parent
      let global = false
      while (p) {
        if (ts.isModuleDeclaration(p) && p.name.text === 'global') global = true
        p = p.parent
      }
      if (global) continue
      if (pageImports.has(name)) {
        const imp = pageImports.get(name)
        imports.push({ name: imp.imported, local: name, from: imp.source })
        continue
      }
      const moduleSym = checker.getSymbolAtLocation(file)
      const exported = moduleSym && checker.getExportsOfModule(moduleSym).some((e) => e.name === name)
      if (!exported) {
        todo = `type ${name} is not exported from ${path.relative(frontendDir, file.fileName)}`
        continue
      }
      const abs = path.resolve(file.fileName)
      let from = packageSpecifier(abs)
      if (!from) {
        const noExt = abs.replace(/(\.d)?\.(ts|tsx|mts)$/, '')
        if (noExt.startsWith(srcDir + path.sep)) from = `@/${path.relative(srcDir, noExt).split(path.sep).join('/')}`
        else {
          from = path.relative(path.dirname(pagePath), noExt).split(path.sep).join('/')
          if (!from.startsWith('.')) from = `./${from}`
        }
      }
      imports.push({ name, local: name, from })
    }
    return { imports, localDecls, todo }
  }

  function describe(type, node) {
    let text = print(type)
    let extraImports = []
    // Types the page cannot name print as import("…").Foo: name them and import them.
    text = text.replace(/import\("([^"]+)"\)\.([\w$]+)/g, (_, file, name) => {
      let from = packageSpecifier(file)
      if (!from) {
        const abs = path.resolve(file)
        from = abs.startsWith(srcDir + path.sep) ? `@/${path.relative(srcDir, abs).split(path.sep).join('/')}` : null
      }
      if (from) extraImports.push({ name, local: name, from })
      return name
    })
    const { imports, localDecls, todo } = importsFor(type, text, node)
    let why = todo
    if (type.flags & ts.TypeFlags.Any) why = 'type resolved to any'
    else if (text.length > 400 && !type.aliasSymbol) why = `anonymous type too large to inline (${text.length} chars)`
    if (why) return { type: 'unknown', imports: [], localDecls: [], todo: why }
    if (/\bany\b/.test(text)) why = null // kept as is; listed by the caller as a soft warning
    return { type: text, imports: [...imports, ...extraImports], localDecls, todo: null }
  }

  for (const name of names) {
    const id = idents.get(name)
    if (!id) {
      result.set(name, fallback('declaration not found in <script setup>'))
      continue
    }
    try {
      let type = checker.getTypeAtLocation(id)
      const ref = refName(type)
      let isRef = false
      let writable = false
      if (ref) {
        const valueSym = type.getProperty('value')
        if (valueSym) {
          type = checker.getTypeOfSymbolAtLocation(valueSym, id)
          isRef = true
          writable = WRITABLE_REF_TYPES.has(ref)
        }
      }
      const sigs = type.getCallSignatures()
      let params = null
      if (sigs.length && !isRef) {
        params = sigs[0].parameters.map((p) => {
          const d = p.valueDeclaration
          const optional = !!(d && ts.isParameter(d) && (d.questionToken || d.initializer))
          const rest = !!(d && ts.isParameter(d) && d.dotDotDotToken)
          const pt = checker.getTypeOfSymbolAtLocation(p, id)
          const described = describe(optional && !rest ? checker.getNonNullableType(pt) : pt, id)
          return { name: p.name, ...described, optional, rest }
        })
      }
      const described = describe(type, id)
      result.set(name, { ...described, isRef, writable, isFunction: sigs.length > 0 && !isRef, params })
    } catch (err) {
      result.set(name, fallback(`type checker failed: ${err.message}`))
    }
  }
  return result
}

// ---------------------------------------------------------------------------
// Router records

function walkFiles(dir, out = []) {
  if (!fs.existsSync(dir)) return out
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walkFiles(p, out)
    else if (/\.ts$/.test(e.name) && !/\.(spec|test)\.ts$/.test(e.name)) out.push(p)
  }
  return out
}

function resolveSpec(spec, fromFile, srcDir) {
  let abs
  if (spec.startsWith('@/')) abs = path.join(srcDir, spec.slice(2))
  else if (spec.startsWith('.')) abs = path.resolve(path.dirname(fromFile), spec)
  else return null
  if (!abs.endsWith('.vue') && fs.existsSync(`${abs}.vue`)) abs = `${abs}.vue`
  return abs
}

/** The router records that render `pagePath`, with whether each already sets `props`. */
export function findRouterRecords(pagePath, { routerDir, srcDir }) {
  const records = []
  for (const file of walkFiles(routerDir)) {
    const text = fs.readFileSync(file, 'utf8')
    if (!text.includes(path.basename(pagePath, '.vue'))) continue
    const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true)
    const locals = new Set()
    for (const stmt of sf.statements) {
      if (ts.isImportDeclaration(stmt) && stmt.importClause?.name && ts.isStringLiteral(stmt.moduleSpecifier)) {
        if (resolveSpec(stmt.moduleSpecifier.text, file, srcDir) === pagePath) locals.add(stmt.importClause.name.text)
      }
    }
    const visit = (node) => {
      let hit = false
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
        const arg = node.arguments[0]
        if (arg && ts.isStringLiteralLike(arg) && resolveSpec(arg.text, file, srcDir) === pagePath) hit = true
      } else if (
        ts.isPropertyAssignment(node) &&
        ts.isIdentifier(node.initializer) &&
        locals.has(node.initializer.text)
      )
        hit = true
      else if (ts.isShorthandPropertyAssignment(node) && locals.has(node.name.text)) hit = true
      if (hit) {
        let p = node
        let prop = null
        while (p && !ts.isObjectLiteralExpression(p)) {
          if (ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) prop = p
          p = p.parent
        }
        if (p && prop && ['component', 'components'].includes(prop.name.getText(sf))) {
          const has = (k) => p.properties.find((x) => x.name && x.name.getText(sf) === k)
          const pathProp = has('path')
          records.push({
            file,
            line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
            hasProps: !!has('props'),
            path:
              pathProp && ts.isPropertyAssignment(pathProp) && ts.isStringLiteralLike(pathProp.initializer)
                ? pathProp.initializer.text
                : null,
            componentEnd: prop.getEnd(),
            componentStart: prop.getStart(sf),
          })
        }
        return
      }
      ts.forEachChild(node, visit)
    }
    visit(sf)
  }
  return records
}

/** Adds `props: true` after the record's `component` property. */
export function addPropsTrue(text, record) {
  let end = record.componentEnd
  const lineStart = text.lastIndexOf('\n', record.componentStart) + 1
  const indent = text.slice(lineStart, record.componentStart)
  const after = text.slice(end)
  const comma = /^\s*,/.exec(after)
  if (comma) end += comma[0].length
  else return `${text.slice(0, end)}, props: true${text.slice(end)}`
  return `${text.slice(0, end)}\n${/^\s*$/.test(indent) ? indent : ' '}props: true,${text.slice(end)}`
}

// ---------------------------------------------------------------------------
// Analysis

/**
 * Builds the plan for one page. `opts`: { frontendDir, srcDir?, routerDir?, tsconfig?, types? }.
 * Throws for pages the tool refuses to read at all (no `<script setup>`, …).
 */
export function analyze(pagePath, opts) {
  const frontendDir = opts.frontendDir
  const srcDir = opts.srcDir ?? path.join(frontendDir, 'src')
  const routerDir = opts.routerDir ?? path.join(srcDir, 'router')
  const tsconfig = opts.tsconfig ?? path.join(frontendDir, 'tsconfig.app.json')
  pagePath = path.resolve(pagePath)
  const source = fs.readFileSync(pagePath, 'utf8')
  const { descriptor, errors } = parseSfc(source, { filename: pagePath })
  if (errors.length) throw new Error(`cannot parse ${pagePath}: ${errors[0].message}`)
  if (!descriptor.scriptSetup) throw new Error('no <script setup>: the split only reads script-setup pages')
  if (!descriptor.template) throw new Error('no <template>')
  if (descriptor.template.lang && descriptor.template.lang !== 'html')
    throw new Error(`<template lang="${descriptor.template.lang}"> is not supported`)
  const compiled = compileScript(descriptor, {
    id: 'scene-split',
    fs: { fileExists: fs.existsSync, readFile: (f) => fs.readFileSync(f, 'utf8') },
  })
  const bindings = compiled.bindings ?? {}
  const imports = compiled.imports ?? {}
  const setupAst = compiled.scriptSetupAst
  const scriptOffset = descriptor.scriptSetup.loc.start.offset
  const decls = setupDeclarations({ body: setupAst })

  const stem = path.basename(pagePath, '.vue')
  const viewName = `${stem}View`
  const viewPath = path.join(path.dirname(pagePath), `${viewName}.vue`)

  const scan = scanTemplate(descriptor.template.ast)
  const unsafe = []
  const warn = (offset, what) => unsafe.push({ line: lineOf(source, offset), what })

  // Template-level things the split cannot carry over.
  for (const r of scan.templateRefs)
    warn(r.offset, `template ref "${r.name}": the element moves to the view, the page's ref stays null`)
  for (const c of scan.dynamicComponents)
    warn(c.offset, '<component :is>: the ratchet cannot verify a dynamic component, check the view by hand')
  for (const s of scan.slotOutlets) warn(s.offset, '<slot>: the page renders no slot content into the view')
  for (const u of scan.uses) if (u.how === 'unparsed') warn(u.offset, `expression did not parse: ${u.detail}`)
  for (const st of descriptor.styles) {
    if (/\bv-bind\s*\(/.test(st.content))
      warn(st.loc.start.offset, '<style> uses v-bind(): the bound names must be props of the view')
  }

  // Group uses by name.
  const byName = new Map()
  for (const u of scan.uses) {
    if (!u.name) continue
    if (!byName.has(u.name)) byName.set(u.name, [])
    byName.get(u.name).push(u)
  }

  // useI18n destructures: `const { t } = useI18n()` -> the view calls useI18n itself.
  const i18nNames = new Set()
  for (const [name, d] of decls) {
    if (d.destructured && calleeName(d.init) === 'useI18n' && d.declarator.id.type === 'ObjectPattern')
      i18nNames.add(name)
  }

  const routeVar = [...decls].find(([, d]) => !d.destructured && calleeName(d.init) === 'useRoute')?.[0] ?? null

  const props = [] // { name, kind: 'value'|'function', ... }
  const models = []
  const emits = []
  const moved = new Map() // source -> { default, namespace, named: Map(imported->local), uses: [] }
  const movedI18n = []
  const unresolved = []

  const moveImport = (local, offset) => {
    const imp = imports[local]
    if (!moved.has(imp.source)) moved.set(imp.source, { source: imp.source, specs: new Map() })
    moved.get(imp.source).specs.set(local, imp.imported)
    if (IMPURE_SOURCE.test(imp.source))
      warn(offset, `"${local}" from '${imp.source}' moves into the view and keeps it from grade A`)
  }

  for (const [name, list] of byName) {
    const first = list[0].offset
    if (name.startsWith('$')) {
      if (name === '$event') continue
      warn(first, UNSAFE_INSTANCE[name] ?? `reads ${name}`)
      continue
    }
    if (imports[name] && !imports[name].isType) {
      moveImport(name, first)
      continue
    }
    if (i18nNames.has(name)) {
      movedI18n.push(name)
      continue
    }
    const decl = decls.get(name)
    const bindingType = bindings[name]
    if (!decl && !bindingType) {
      if (!TEMPLATE_GLOBALS.has(name)) {
        unresolved.push(name)
        warn(first, `"${name}" is not a binding of this page (global property?)`)
      }
      continue
    }
    if (name === routeVar) {
      warn(first, `the template reads the route ("${name}"): pass the values it needs as props instead`)
    }
    if (bindingType === 'props' || bindingType === 'props-aliased') {
      props.push({ name, kind: 'value', fromPageProps: true, uses: list })
      continue
    }
    const isFn = decl?.kind === 'function'
    if (isFn) {
      const nonEvent = list.filter((u) => !(u.ctx === 'handler' && (u.how === 'call' || u.how === 'bare')))
      if (nonEvent.length === 0) {
        emits.push({ name, event: eventName(name), uses: list, params: decl.params })
      } else {
        for (const u of nonEvent)
          warn(
            u.offset,
            `setup function "${name}" is used outside an event handler: passed to the view as a function prop`
          )
        props.push({ name, kind: 'function', uses: list })
      }
      continue
    }
    const assigned = list.filter((u) => u.how === 'assign')
    const mutated = list.filter((u) => u.how === 'mutate')
    if (assigned.length) {
      models.push({ name, uses: list, bindingType, declKind: decl?.kind })
      continue
    }
    for (const u of mutated)
      warn(u.offset, `the template mutates "${name}" in place: the view would be writing into a prop`)
    if (RESERVED_PROPS.has(name)) warn(first, `"${name}" cannot be a prop name: rename it in the page first`)
    props.push({ name, kind: 'value', uses: list, bindingType })
  }

  // Component tags and custom directives resolve to imports.
  const tagNames = new Set()
  for (const t of scan.tags) {
    const candidates = [t.tag, pascal(t.tag), camel(t.tag)]
    const local = candidates.find((c) => imports[c] && !imports[c].isType)
    if (local) {
      tagNames.add(local)
      moveImport(local, t.offset)
    } else if (candidates.some((c) => decls.has(c))) {
      warn(t.offset, `<${t.tag}> is a component declared in the page's setup, not imported: move it by hand`)
    }
  }
  for (const d of scan.directives) {
    const local = `v${pascal(d.name)}`
    if (imports[local] && !imports[local].isType) moveImport(local, d.offset)
  }

  // Types.
  const pageImports = new Map()
  for (const [local, imp] of Object.entries(imports))
    pageImports.set(local, { imported: imp.imported, source: imp.source })
  const typeNames = [...props.map((p) => p.name), ...models.map((m) => m.name), ...emits.map((e) => e.name)]
  const types =
    opts.types === false
      ? new Map()
      : resolveTypes({
          pagePath,
          scriptContent: descriptor.scriptSetup.content,
          names: typeNames,
          srcDir,
          frontendDir,
          tsconfig,
          pageImports,
        })
  const typeOf = (name) =>
    types.get(name) ?? {
      type: 'unknown',
      isRef: false,
      writable: false,
      isFunction: false,
      params: null,
      imports: [],
      localDecls: [],
      todo: 'types skipped',
    }

  for (const m of models) {
    const t = typeOf(m.name)
    const isRef = t.isRef ? t.writable : m.bindingType === 'setup-ref'
    m.type = t
    if (!isRef) {
      for (const u of m.uses.filter((x) => x.how === 'assign'))
        warn(u.offset, `the template assigns "${m.name}", which is not a writable ref: the view cannot model it`)
    }
  }
  for (const p of props) p.type = typeOf(p.name)
  for (const e of emits) {
    const t = typeOf(e.name)
    e.type = t
    e.args = (
      t.params ??
      (e.params ?? []).map((p) => ({
        name: patternNames(p)[0] ?? 'arg',
        type: 'unknown',
        todo: 'no signature',
        imports: [],
        localDecls: [],
      }))
    ).map((p, i) => ({ ...p, name: /^[A-Za-z_$][\w$]*$/.test(p.name) ? p.name : `arg${i}` }))
  }

  // Route reads.
  const route = { variable: routeVar, params: [], other: [], rewritable: false, records: [], pageHasProps: false }
  if (routeVar) {
    const scriptSrc = descriptor.scriptSetup.content
    const walk = (node, parent, stack) => {
      if (!node || typeof node.type !== 'string') return
      if (
        node.type === 'Identifier' &&
        node.name === routeVar &&
        parent &&
        !(parent.type === 'VariableDeclarator' && parent.id === node)
      ) {
        const isObj =
          (parent.type === 'MemberExpression' || parent.type === 'OptionalMemberExpression') && parent.object === node
        const prop = isObj && !parent.computed ? parent.property.name : null
        // `stack` ends with [parent, node], so the node that holds `route.params`
        // — the read the rewrite replaces — is the one above the parent.
        const outer = stack[stack.length - 3]
        const offset = scriptOffset + node.start
        if (
          prop === 'params' &&
          outer &&
          (outer.type === 'MemberExpression' || outer.type === 'OptionalMemberExpression') &&
          outer.object === parent
        ) {
          const key = outer.computed
            ? outer.property.type === 'StringLiteral'
              ? outer.property.value
              : null
            : outer.property.name
          route.params.push({
            key,
            start: node.start,
            end: outer.end,
            line: lineOf(source, offset),
            text: scriptSrc.slice(node.start, outer.end),
          })
        } else {
          const where = prop ? `${routeVar}.${prop}` : routeVar
          route.other.push({
            what: where,
            line: lineOf(source, offset),
            text: scriptSrc.slice(node.start, isObj ? parent.end : node.end),
          })
        }
      }
      for (const k of Object.keys(node)) {
        if (k === 'loc' || k === 'leadingComments' || k === 'trailingComments') continue
        const v = node[k]
        if (Array.isArray(v)) v.forEach((c) => c && typeof c === 'object' && walk(c, node, [...stack, c]))
        else if (v && typeof v === 'object' && typeof v.type === 'string') walk(v, node, [...stack, v])
      }
    }
    for (const stmt of setupAst) walk(stmt, null, [stmt])
    route.pageHasProps =
      Object.values(bindings).some((b) => b === 'props') || /\bdefineProps\s*[<(]/.test(scriptSrc) || decls.has('props')
    route.rewritable =
      route.params.length > 0 &&
      route.params.every((p) => p.key && /^[A-Za-z_$][\w$]*$/.test(p.key)) &&
      !route.pageHasProps
    if (route.params.length) {
      route.records = findRouterRecords(pagePath, { routerDir, srcDir })
    }
  }

  return {
    page: pagePath,
    stem,
    viewName,
    viewPath,
    viewExists: fs.existsSync(viewPath),
    source,
    descriptor,
    compiled,
    decls,
    bindings,
    imports,
    props,
    models,
    emits,
    moved: [...moved.values()],
    i18n: movedI18n,
    unsafe: unsafe.sort((a, b) => a.line - b.line),
    unresolved,
    scan,
    route,
    tagNames,
  }
}

// ---------------------------------------------------------------------------
// Generation

function importLine(source, specs) {
  let def = null
  let ns = null
  const named = []
  for (const [local, imported] of specs) {
    if (imported === 'default') def = local
    else if (imported === '*') ns = local
    else named.push(imported === local ? local : `${imported} as ${local}`)
  }
  const parts = []
  if (def) parts.push(def)
  if (ns) parts.push(`* as ${ns}`)
  if (named.length) parts.push(`{ ${named.join(', ')} }`)
  return `import ${parts.join(', ')} from '${source}'`
}

function propKey(name) {
  return /^[A-Za-z_$][\w$]*$/.test(name) ? name : `'${name}'`
}

function typeText(t) {
  return t.todo ? `unknown // TODO(scene-split): type (${t.todo})` : t.type
}

/** Rewrites the v-on expressions of the template for the view; returns the new template inner text. */
function viewTemplate(plan) {
  const { source, descriptor } = plan
  const start = descriptor.template.loc.start.offset
  const end = descriptor.template.loc.end.offset
  const s = new MagicString(source)
  const emitByName = new Map(plan.emits.map((e) => [e.name, e]))
  for (const e of plan.emits) {
    for (const u of e.uses) {
      const ex = plan.scan.exprs[u.exprIndex]
      const at = (offsetInContent) => ex.base + offsetInContent - ex.parsed.shift
      if (u.how === 'bare') {
        const args = e.args
        let text
        if (!args.length) text = `emit('${e.event}')`
        else if (args.length === 1 && !args[0].rest) text = `emit('${e.event}', $event)`
        else {
          const names = args.map((a) => (a.rest ? `...${a.name}` : a.name))
          text = `(${names.join(', ')}) => emit('${e.event}', ${names.join(', ')})`
        }
        s.overwrite(ex.base, ex.base + ex.content.length, text)
      } else if (u.how === 'call') {
        const call = u.parent
        s.overwrite(at(u.node.start), at(u.node.end), 'emit')
        if (call.arguments.length) s.appendLeft(at(call.arguments[0].start), `'${e.event}', `)
        else s.appendLeft(at(call.end) - 1, `'${e.event}'`)
      }
    }
  }
  void emitByName
  return s.slice(start, end)
}

/** Generates `{ view, page, router }` sources for a plan. */
export function generate(plan, opts = {}) {
  const { descriptor, source } = plan
  // ---- the view ----
  const typeImports = new Map() // from -> Map(local -> imported)
  const localDecls = new Set()
  const addTypes = (t) => {
    if (!t || t.todo) return
    for (const imp of t.imports) {
      if (!typeImports.has(imp.from)) typeImports.set(imp.from, new Map())
      typeImports.get(imp.from).set(imp.local, imp.name)
    }
    for (const d of t.localDecls) localDecls.add(d)
  }
  plan.props.forEach((p) => addTypes(p.type))
  plan.models.forEach((m) => addTypes(m.type))
  plan.emits.forEach((e) => e.args.forEach(addTypes))

  const lines = []
  for (const [from, specs] of typeImports) {
    // A name the view also value-imports needs no separate type import.
    const movedHere = plan.moved.find((m) => m.source === from)
    const rest = new Map([...specs].filter(([local]) => !movedHere?.specs.has(local)))
    if (rest.size) lines.push(importLine(from, rest).replace(/^import /, 'import type '))
  }
  if (plan.i18n.length) lines.push(`import { useI18n } from 'vue-i18n'`)
  for (const m of plan.moved) lines.push(importLine(m.source, m.specs))
  if (localDecls.size) lines.push('', ...localDecls)
  lines.push('')
  const propLines = plan.props.map((p) => `  ${propKey(p.name)}: ${typeText(p.type)}`)
  if (propLines.length) lines.push('defineProps<{', ...propLines, '}>()')
  if (plan.emits.length) {
    const emitLines = plan.emits.map((e) => {
      const args = e.args.map(
        (a) => `${a.rest ? '...' : ''}${a.name}${a.optional && !a.rest ? '?' : ''}: ${a.todo ? 'unknown' : a.type}`
      )
      const todo = e.args.some((a) => a.todo) ? ' // TODO(scene-split): type' : ''
      return `  ${propKey(e.event)}: [${args.join(', ')}]${todo}`
    })
    lines.push('const emit = defineEmits<{', ...emitLines, '}>()')
  }
  for (const m of plan.models) {
    const t = m.type
    lines.push(
      `const ${m.name} = defineModel<${t.todo ? 'unknown' : t.type}>('${m.name}', { required: true })${t.todo ? ' // TODO(scene-split): type' : ''}`
    )
  }
  if (plan.i18n.length) lines.push(`const { ${plan.i18n.join(', ')} } = useI18n()`)

  const rel = path.basename(plan.page)
  const header = [
    '<!--',
    `  The rendering half of ${rel}, split out by \`pnpm run scene:split\`: props in,`,
    '  events out. The page keeps the route, the requests and the stores.',
    '-->',
  ].join('\n')
  const styles = descriptor.styles.map((st) =>
    source.slice(
      st.loc.start.offset - openTagLength(source, st.loc.start.offset),
      closeTagEnd(source, st.loc.end.offset)
    )
  )
  const view =
    [
      header,
      `<script setup lang="ts">\n${lines.join('\n')}\n</script>`,
      `<template>${viewTemplate(plan)}</template>`,
      ...styles,
    ].join('\n\n') + '\n'

  // ---- the page ----
  let script = descriptor.scriptSetup.content
  const scriptEdits = new MagicString(script)
  const route = plan.route
  const routeRewrite = route.rewritable && (opts.routeRewrite ?? true)
  const propsDecl = []
  if (routeRewrite) {
    const keys = [...new Set(route.params.map((p) => p.key))]
    const record = route.records.length === 1 ? route.records[0] : null
    for (const p of route.params) scriptEdits.overwrite(p.start, p.end, `props.${p.key}`)
    const optional = (k) => (record?.path ? new RegExp(`:${k}(\\([^)]*\\))?\\?`).test(record.path) : false)
    propsDecl.push(
      `const props = defineProps<{ ${keys.map((k) => `${k}${optional(k) ? '?' : ''}: string`).join('; ')} }>()`
    )
  }
  script = scriptEdits.toString()
  // Insert defineProps after the last import.
  if (propsDecl.length) {
    const ast = babelParse(script, { plugins: ['typescript'], sourceType: 'module' }).program
    const lastImport = [...ast.body].reverse().find((n) => n.type === 'ImportDeclaration')
    const at = lastImport ? lastImport.end : 0
    script = `${script.slice(0, at)}\n\n${propsDecl.join('\n')}${script.slice(at)}`
  }
  // The page now renders the view: import it, drop what only the template used.
  const pageTemplateNames = new Set([...plan.props, ...plan.models, ...plan.emits].map((x) => x.name))
  script = pruneScript(script, pageTemplateNames, plan, `import ${plan.viewName} from './${plan.viewName}.vue'`)

  const attrs = []
  for (const p of plan.props) attrs.push(`:${kebab(p.name)}="${p.name}"`)
  for (const m of plan.models) attrs.push(`v-model:${m.name}="${m.name}"`)
  for (const e of plan.emits) attrs.push(`@${e.event}="${e.name}"`)
  const pageTemplate = attrs.length
    ? `\n  <${plan.viewName}\n${attrs.map((a) => `    ${a}`).join('\n')}\n  />\n`
    : `\n  <${plan.viewName} />\n`

  // Splice: script setup content, template content, drop styles.
  const page = new MagicString(source)
  page.overwrite(descriptor.scriptSetup.loc.start.offset, descriptor.scriptSetup.loc.end.offset, ensureNewlines(script))
  page.overwrite(descriptor.template.loc.start.offset, descriptor.template.loc.end.offset, pageTemplate)
  for (const st of descriptor.styles) {
    const from = st.loc.start.offset - openTagLength(source, st.loc.start.offset)
    let to = closeTagEnd(source, st.loc.end.offset)
    while (source[to] === '\n') to++
    let back = from
    while (back > 0 && source[back - 1] === '\n') back--
    page.remove(back, to)
  }
  let pageText = page.toString()
  if (!pageText.endsWith('\n')) pageText += '\n'

  // ---- the router ----
  let router = null
  if (routeRewrite && route.records.length === 1 && !route.records[0].hasProps) {
    const rec = route.records[0]
    router = { file: rec.file, text: addPropsTrue(fs.readFileSync(rec.file, 'utf8'), rec) }
  }
  return { view, page: pageText, router }
}

function ensureNewlines(s) {
  return `\n${s.replace(/^\n+/, '').replace(/\s+$/, '')}\n`
}

function openTagLength(source, contentStart) {
  const open = source.lastIndexOf('<style', contentStart)
  return contentStart - open
}

function closeTagEnd(source, contentEnd) {
  const close = source.indexOf('</style>', contentEnd)
  return close + '</style>'.length
}

/**
 * Drops import specifiers and `useI18n` / `useRoute` declarations that nothing in
 * the page reads any more, and adds the view import.
 */
function pruneScript(script, templateNames, plan, viewImport) {
  for (let pass = 0; pass < 3; pass++) {
    const ast = babelParse(script, { plugins: ['typescript'], sourceType: 'module' }).program
    const used = scriptReferences(ast)
    for (const n of templateNames) used.add(n)
    const s = new MagicString(script)
    let changed = false
    for (const stmt of ast.body) {
      if (stmt.type === 'ImportDeclaration') {
        if (stmt.importKind === 'type' || !stmt.specifiers.length) continue
        const keep = stmt.specifiers.filter((sp) => sp.importKind === 'type' || used.has(sp.local.name))
        if (keep.length === stmt.specifiers.length) continue
        changed = true
        if (!keep.length) {
          s.remove(stmt.start, script[stmt.end] === '\n' ? stmt.end + 1 : stmt.end)
          continue
        }
        const specs = new Map()
        const typeOnly = []
        for (const sp of keep) {
          if (sp.type === 'ImportDefaultSpecifier') specs.set(sp.local.name, 'default')
          else if (sp.type === 'ImportNamespaceSpecifier') specs.set(sp.local.name, '*')
          else if (sp.importKind === 'type') typeOnly.push(sp)
          else specs.set(sp.local.name, sp.imported.name ?? sp.imported.value)
        }
        let line = importLine(stmt.source.value, specs)
        if (typeOnly.length) {
          const t = typeOnly.map(
            (sp) =>
              `type ${sp.imported.name === sp.local.name ? sp.local.name : `${sp.imported.name} as ${sp.local.name}`}`
          )
          line = line.includes('{')
            ? line.replace(/\{ /, `{ ${t.join(', ')}, `)
            : line.replace(/ from /, `${specs.size ? ', ' : ' '}{ ${t.join(', ')} } from `)
        }
        s.overwrite(stmt.start, stmt.end, line)
      } else if (stmt.type === 'VariableDeclaration' && stmt.declarations.length === 1) {
        const d = stmt.declarations[0]
        const callee = calleeName(d.init)
        if (callee !== 'useI18n' && callee !== 'useRoute') continue
        const names = patternNames(d.id)
        const unusedNames = names.filter((n) => !used.has(n))
        if (!unusedNames.length) continue
        changed = true
        if (unusedNames.length === names.length) {
          s.remove(stmt.start, script[stmt.end] === '\n' ? stmt.end + 1 : stmt.end)
        } else if (d.id.type === 'ObjectPattern') {
          const kept = d.id.properties.filter(
            (p) => !(p.type === 'ObjectProperty' && unusedNames.includes(patternNames(p.value)[0]))
          )
          s.overwrite(d.id.start, d.id.end, `{ ${kept.map((p) => script.slice(p.start, p.end)).join(', ')} }`)
        }
      }
    }
    script = s.toString()
    if (!changed) break
  }
  // Add the view import after the last import.
  const ast = babelParse(script, { plugins: ['typescript'], sourceType: 'module' }).program
  const lastImport = [...ast.body].reverse().find((n) => n.type === 'ImportDeclaration')
  const at = lastImport ? lastImport.end : 0
  return `${script.slice(0, at)}${at ? '\n' : ''}${viewImport}${at ? '' : '\n'}${script.slice(at)}`
}

// ---------------------------------------------------------------------------
// Reporting

function fmtType(t) {
  return t.todo ? `unknown (TODO: ${t.todo})` : t.type
}

export function toJson(plan) {
  return {
    page: plan.page,
    view: plan.viewPath,
    viewExists: plan.viewExists,
    props: plan.props.map((p) => ({
      name: p.name,
      kind: p.kind,
      type: p.type.todo ? 'unknown' : p.type.type,
      todo: p.type.todo,
    })),
    models: plan.models.map((m) => ({ name: m.name, type: m.type.todo ? 'unknown' : m.type.type, todo: m.type.todo })),
    emits: plan.emits.map((e) => ({
      handler: e.name,
      event: e.event,
      args: e.args.map((a) => ({
        name: a.name,
        type: a.todo ? 'unknown' : a.type,
        optional: !!a.optional,
        rest: !!a.rest,
      })),
    })),
    movedImports: plan.moved.map((m) => ({ source: m.source, names: [...m.specs.keys()] })),
    i18n: plan.i18n,
    route: {
      variable: plan.route.variable,
      params: plan.route.params.map((p) => ({ key: p.key, line: p.line, text: p.text })),
      other: plan.route.other,
      rewritable: plan.route.rewritable,
      pageHasProps: plan.route.pageHasProps,
      records: plan.route.records.map((r) => ({ file: r.file, line: r.line, hasProps: r.hasProps, path: r.path })),
    },
    unsafe: plan.unsafe,
    typeTodos: typeTodos(plan),
  }
}

function typeTodos(plan) {
  const out = []
  for (const p of plan.props) if (p.type.todo) out.push({ name: p.name, why: p.type.todo })
  for (const m of plan.models) if (m.type.todo) out.push({ name: m.name, why: m.type.todo })
  for (const e of plan.emits)
    for (const a of e.args) if (a.todo) out.push({ name: `${e.name}(${a.name})`, why: a.todo })
  return out
}

export function formatReport(plan, rel = (p) => p) {
  const out = []
  out.push(
    `scene:split ${rel(plan.page)} -> ${rel(plan.viewPath)}${plan.viewExists ? '  (EXISTS: --write refuses)' : ''}`
  )
  out.push('')
  out.push(`props (${plan.props.length})`)
  for (const p of plan.props)
    out.push(`  ${p.name}: ${fmtType(p.type)}${p.kind === 'function' ? '   [function prop]' : ''}`)
  out.push(`models (${plan.models.length})`)
  for (const m of plan.models) out.push(`  v-model:${m.name}: ${fmtType(m.type)}`)
  out.push(`emits (${plan.emits.length})`)
  for (const e of plan.emits)
    out.push(`  @${e.event} -> ${e.name}(${e.args.map((a) => `${a.name}: ${a.todo ? 'unknown' : a.type}`).join(', ')})`)
  out.push(`moved imports (${plan.moved.reduce((n, m) => n + m.specs.size, 0) + plan.i18n.length})`)
  for (const m of plan.moved) out.push(`  ${[...m.specs.keys()].join(', ')}  from '${m.source}'`)
  if (plan.i18n.length) out.push(`  ${plan.i18n.join(', ')}  from useI18n() (the view calls it itself)`)
  const r = plan.route
  if (r.variable) {
    out.push(`route (${r.variable} = useRoute())`)
    for (const p of r.params) out.push(`  L${p.line} ${p.text}${r.rewritable ? ` -> props.${p.key}` : ''}`)
    if (r.params.length && !r.rewritable) {
      out.push(
        r.pageHasProps
          ? '  not rewritten: the page already declares props'
          : '  not rewritten: a params read is not a plain route.params.X'
      )
    }
    for (const o of r.other) out.push(`  L${o.line} ${o.text}  (report only)`)
    if (r.params.length) {
      if (!r.records.length) out.push('  router record: not found')
      else if (r.records.length > 1)
        out.push(`  router record: ${r.records.length} records render this page, not touched`)
      for (const rec of r.records)
        out.push(
          `  router record ${rel(rec.file)}:${rec.line} path=${rec.path ?? '?'} ${rec.hasProps ? 'already has props' : r.records.length === 1 && r.rewritable ? 'no props -> add props: true' : 'no props'}`
        )
    }
  }
  const todos = typeTodos(plan)
  if (todos.length) {
    out.push(`types left as unknown (${todos.length})`)
    for (const t of todos) out.push(`  ${t.name}: ${t.why}`)
  }
  out.push(`needs a human (${plan.unsafe.length})`)
  for (const u of plan.unsafe) out.push(`  L${u.line} ${u.what}`)
  return out.join('\n')
}
