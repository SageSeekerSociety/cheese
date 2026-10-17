// A call a component binds nothing for, to a name a module right next to it
// keeps to itself.
//
// THE RULE. A `.vue` component may call a bare name only when it has something
// of its own for that name — a `<script setup>` binding, a prop, a method, a
// template local. When it has nothing, the call does not fail to resolve: it
// silently becomes a call to a browser global. The author meant a function
// one module away that never got exported, or a function this component never
// imported. Either way the line reads as a call and calls something else.
//
// WHY NOTHING ELSE CATCHES IT. `no-undef` would, but the config injects
// `globals.browser` (eslint.config.mjs), and the names composables actually
// define are the ones the browser already has: `blur`, `close`, `focus`,
// `open`, `print`, `scrollTo`, `stop`, `confirm`, `status`. In TS files
// `@typescript-eslint` turns `no-undef` off and defers to the compiler, and
// `lib.dom` declares the same names, so `vue-tsc` is silent too. Templates are
// referenced by neither: `vue-eslint-parser` builds the template AST but adds
// no references to it, so what a template calls is invisible to both.
//
// On 2026-10-16 (#1618) `blur()` — which drops the space bar a panel was
// holding down — moved into `src/components/panels/preview/useDesignViewport.ts`
// and was never handed back out of it: the function sits in the body of
// `useDesignViewport`. `DesignImage.vue`'s `if (!on) blur()` called
// `window.blur`, did nothing, and no check and no test said a word.
//
// WHY THE CALL, NOT EVERY IDENTIFIER. The binding that gets lost is always a
// function, and a template read of a name that resolves to nothing renders
// `undefined` instead of doing the wrong thing quietly. Judging `foo(…)` with a
// bare identifier callee also keeps the rule exact about what it is not
// judging: `el.focus()` and `window.print()` are spelled out and correct.
//
// WHY THE SAME-TREE CONDITION. A bare call is only worth reporting when some
// module in this repo declares that name and does not hand it out — which is
// both what makes the finding actionable (the report names the file that kept
// the name to itself) and what keeps the rule from re-stating `no-undef` over
// every browser global. A name no module here declares is either a global the
// author meant, or a typo in a component that never had the binding; neither
// is the confusion this rule exists to end.
//
// At any depth in that module, not only at its top level. The refactor this
// rule is about declared the function inside the composable it moved to, so a
// rule reading the module's top-level statements judged the file it was written
// about clean — measured, not assumed: with the top-level reading only, the
// #1618 pair above reports nothing, and the same declarations moved to module
// level report both names. Everything is read instead, and a name is "kept to
// itself" exactly when no `export` hands it out.
//
// WHY THE TEMPLATE HALF IS LOOSE, BUT NOT BLANKET. A template resolves a name
// that is not a `<script setup>` binding through the render context, which
// reaches props, data, methods and computed — declared in the script but not as
// module bindings — and whatever the app registers for templates besides. So a
// name the script mentions counts as bound. Read strictly instead — by what the
// script binds — and the tree reports `isMine(m)` in ChatTimeline.vue and
// `rowTo(task)` in TasksView.vue, each a function prop of the component's own
// `defineProps<{ … }>()`.
//
// What a string may be is a name in a place that declares one: a prop key, or
// an entry in a list of prop names. Every other literal in the script would
// qualify too, and labels, i18n values and event lists all spell these names —
// a call that binds nothing reads as bound whenever some string in the file
// happens to spell it, and the rule answers "clean" to exactly the call it
// exists to find. Excluding those costs nothing on today's tree — 0 findings
// either way, measured. `stringBindings` reads the places a string can be one:
// the argument of the props macro, as a list (`defineProps(['open'])`) or as an
// object with quoted keys; the name a model is declared under; and the quoted
// names in this component's own options object. An unquoted key was already a
// mention, as every identifier is. What stays loose is a name merely mentioned.
//
// Note on which files are read: the two halves are one DSL read twice, so the
// module and component globs, and the ignored spec files, are declared here
// next to the rule rather than only in the runner.

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import vueParser from 'vue-eslint-parser'
import { parser as tsParser } from 'typescript-eslint'

/** Where a call can be: the template, or `<script setup>`. */
export const TEMPLATE = 'template'
export const SCRIPT = 'script setup'

// The frontend root comes from this file's own location (scripts/…), never
// from `process.cwd()`: the runner is invoked from `frontend/`, from the repo
// root through `task`, and from a test, and a rule that judged the wrong tree
// would report nothing while looking green.
const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/** The modules whose declarations can be the lost binding. */
export const GLOBAL_FALLBACK_SOURCES = ['src/**/*.ts']

/** The files judged as call sites. */
export const GLOBAL_FALLBACK_FILES = ['src/**/*.vue']

/** A test mounts a component and calls into it on purpose; its names are its own. */
export const GLOBAL_FALLBACK_IGNORES = ['**/*.spec.ts', '**/*.test.ts', '**/*.spec.vue', '**/*.test.vue', '**/*.d.ts']

/**
 * The files under the frontend root matching one of `patterns`, sorted.
 *
 * A hand-rolled walk rather than a glob package: the tree is 1700 files, the
 * patterns above are two directories and four suffixes, and a new runtime
 * dependency for that would be the larger cost. `**` matches any depth, `*`
 * matches within one path segment, and a pattern without a slash is matched
 * against the basename.
 */
export function collectFiles(patterns, { root = FRONTEND_ROOT, ignores = [] } = {}) {
  const wanted = patterns.map(compileGlob)
  const unwanted = ignores.map(compileGlob)
  const found = []

  const visit = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const absolute = join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules') continue
        visit(absolute)
        continue
      }
      if (!entry.isFile()) continue
      const path = relative(root, absolute).split('\\').join('/')
      if (unwanted.some((match) => match(path))) continue
      if (!wanted.some((match) => match(path))) continue
      found.push(path)
    }
  }

  if (existsSync(root)) visit(root)
  return found.sort()
}

// Turn one glob into a path predicate: two stars span directories, one star
// stops at a slash. Written without the glob spelled literally — a `*` next to
// a `/` ends a doc comment, which is how the first draft of this file failed to
// parse at all.
//
// One pass over the three tokens, and not three passes over the string: a
// rewrite that ran `.*` for two stars first would hand the single-star rule a
// fresh star to eat, and `src/**/*.vue` would come out matching one directory
// level instead of any. It did, in the first draft: the walk saw 71 components
// of 751 and this rule reported a clean tree it had barely looked at.
function compileGlob(pattern) {
  const source = pattern
    .replace(/[.+^$(){}|[\]\\]/g, (char) => `\\${char}`)
    .replace(/\*\*\/|\*\*|\*/g, (token) => (token === '**/' ? '(?:.*/)?' : token === '**' ? '.*' : '[^/]*'))
  const anchored = pattern.includes('/') ? `^${source}$` : `^(?:.*/)?${source}$`
  const re = new RegExp(anchored)
  return (path) => re.test(path)
}

/** Keys in a report are tree-relative, so two runners cannot disagree on one file. */
export function relativeToRoot(absolute) {
  return relative(FRONTEND_ROOT, absolute).split('\\').join('/')
}

/** Every name a binding pattern introduces (`row`, `{ id }`, `[a, b]`, `x = 1`). */
function patternNames(node, out) {
  if (!node) return out
  switch (node.type) {
    case 'Identifier':
      out.push(node.name)
      break
    case 'ObjectPattern':
      for (const property of node.properties) {
        if (property.type === 'RestElement') patternNames(property.argument, out)
        else patternNames(property.value, out)
      }
      break
    case 'ArrayPattern':
      for (const element of node.elements) patternNames(element, out)
      break
    case 'AssignmentPattern':
      patternNames(node.left, out)
      break
    case 'RestElement':
      patternNames(node.argument, out)
      break
    default:
      break
  }
  return out
}

/** The names one declaration statement declares; nothing for an erased one. */
function declaredNames(node) {
  const out = []
  switch (node.type) {
    case 'FunctionDeclaration':
    case 'ClassDeclaration':
    case 'TSEnumDeclaration':
      if (node.id?.name) out.push(node.id.name)
      break
    case 'VariableDeclaration':
      for (const declarator of node.declarations) patternNames(declarator.id, out)
      break
    // An interface, a type alias or a namespace is erased before anything
    // runs: it cannot be called, and a compiler already owns whether it was
    // imported.
    default:
      break
  }
  return out
}

/**
 * The names a module declares and never hands out: not by naming them on a
 * declaration (`export function f`), and not through a later `export { f }`.
 *
 * Every declaration in the module counts, at any depth — not only the ones at
 * the top level. The refactor this rule exists for (#1618) put the function
 * inside the composable it moved to, so `blur` sits in the body of
 * `useDesignViewport`, not at module level; a rule that read `ast.body` alone
 * judged that very file clean. A name inside a function is no less "a name this
 * module keeps to itself": an `export` is still the only way any of them
 * leaves the module.
 *
 * A default export counts as handed out — `export default function f()` is a
 * binding a caller can hold under another name. `export default f` hands out
 * the expression, so `f` itself stays internal unless a specifier names it.
 * @param {string} code the module source
 * @returns {string[]} the internal names, in declaration order, each once
 */
export function internalDeclarations(code) {
  const { ast } = tsParser.parseForESLint(code, { sourceType: 'module', ecmaVersion: 'latest' })
  const exported = new Set()
  const internal = []

  for (const node of ast.body) {
    if (node.type === 'ExportNamedDeclaration') {
      if (node.declaration) for (const name of declaredNames(node.declaration)) exported.add(name)
      for (const specifier of node.specifiers ?? []) {
        if (specifier.local?.name) exported.add(specifier.local.name)
      }
      continue
    }
    if (node.type === 'ExportDefaultDeclaration') {
      if (node.declaration?.id?.name) exported.add(node.declaration.id.name)
      continue
    }
  }

  // A namespace body is not read, and nothing in one is indexed. From another
  // module a member is reached as `TeamsApi.create` — bare only inside the
  // namespace's own body, where no template can be — so no bare call can land
  // on one, exported or not. Reading the bodies listed every member of the
  // eleven `src/api` namespaces as a name its module "keeps to itself", which
  // is the opposite of what `export namespace` means and named a repair that
  // would not apply.
  for (const node of walk(ast, (visited) => visited.type === 'TSModuleDeclaration')) {
    internal.push(...declaredNames(node))
  }

  // One module declares a name many times over once the nested scopes are read
  // (`const host` inside five functions); the index this feeds lists a module
  // once per name it declares.
  return [...new Set(internal)].filter((name) => !exported.has(name))
}

/**
 * Every name a module index maps to the modules declaring it, tree-relative
 * and sorted per name.
 * @param {{path: string, code: string}[]} modules
 */
export function collectDefinitions(modules) {
  const definitions = new Map()
  for (const { path, code } of modules) {
    for (const name of internalDeclarations(code)) {
      if (!definitions.has(name)) definitions.set(name, [])
      definitions.get(name).push(path)
    }
  }
  for (const files of definitions.values()) files.sort()
  return definitions
}

/**
 * Depth-first walk over an AST, which carries `parent` links back up.
 *
 * A node `past` says true for is yielded and not descended into: the one
 * caller that prunes is `internalDeclarations`, over a namespace body.
 */
function* walk(node, past = () => false) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const child of node) yield* walk(child, past)
    return
  }
  if (typeof node.type === 'string') {
    yield node
    if (past(node)) return
  }
  for (const [key, value] of Object.entries(node)) {
    if (key === 'parent' || key === 'references' || key === 'tokens' || key === 'comments') continue
    if (value && typeof value === 'object') yield* walk(value, past)
  }
}

/**
 * Every name the component's script mentions at all: a binding, an import, a
 * property key, a member, a member of a type.
 *
 * The template half needs this because a template's render context is not the
 * module scope — see the note at the top. "The script mentions it" is what
 * this rule reads as "the component has something for the template to call".
 *
 * What it deliberately does not count is a string. Every literal in the script
 * would read as a mention, and labels, i18n keys and event lists all spell
 * these names: a call that binds nothing reads as bound whenever some string in
 * the file happens to spell it. The string forms that really do declare a name
 * for the template are read for what they are, in `stringBindings`.
 */
function scriptMentions(body) {
  const names = new Set()
  for (const node of walk(body)) {
    if (node.type === 'Identifier' && node.name) names.add(node.name)
    if (node.type === 'TSPropertySignature' && node.key) {
      const key = node.key.name ?? node.key.value
      if (typeof key === 'string') names.add(key)
    }
  }
  return names
}

/** What a TypeScript assertion wraps: `['open'] as const` is still an array. */
function unassert(node) {
  let current = node
  while (
    current?.type === 'TSAsExpression' ||
    current?.type === 'TSSatisfiesExpression' ||
    current?.type === 'TSNonNullExpression' ||
    current?.type === 'TSTypeAssertion'
  ) {
    current = current.expression
  }
  return current
}

/** The names an object literal spells in quotes: `{ 'open': … }`, `{ ['open']: … }`. */
function quotedKeys(node) {
  const object = unassert(node)
  if (object?.type !== 'ObjectExpression') return []
  return object.properties.flatMap((property) =>
    property.type === 'Property' && property.key?.type === 'Literal' && typeof property.key.value === 'string'
      ? [property.key.value]
      : []
  )
}

/** The name a property is written under, quoted or not. */
function propertyName(key) {
  if (key?.type === 'Identifier') return key.name
  if (key?.type === 'Literal' && typeof key.value === 'string') return key.value
  return undefined
}

/**
 * Reads an identifier as the value this file gives its binding, through the
 * bindings eslint-scope built for the same parse.
 *
 * Whether a name means the module's variable or a local one is a question
 * about scope, and scope is exactly what the parser already answered: a
 * block-scoped `const` that has ended by the time the name is read, a
 * parameter, a destructured name, a `var` hoisted out of a block, a namespace
 * member that is reachable only as `NS.member` — reading declarations out of
 * statement lists gets every one of those wrong in one direction or the other.
 *
 * Returns the initializer when the binding has one written here, `null` when
 * the name is bound to something whose value this file does not spell (a
 * parameter, an import, a function), and `undefined` when the identifier
 * resolves to no binding at all.
 */
function bindingResolver(scopeManager) {
  const bindings = new Map()
  for (const scope of scopeManager.scopes) {
    for (const reference of scope.references) {
      if (reference.identifier) bindings.set(reference.identifier, reference.resolved ?? null)
    }
  }
  return (identifier) => {
    const variable = bindings.get(identifier)
    if (!variable) return undefined
    for (const def of variable.defs ?? []) {
      if (def.type === 'Variable' && def.node?.init) return def.node.init
    }
    // `let options; options = { … }` — the value is assigned after the
    // declaration, and only an assignment to this binding counts.
    for (const reference of variable.references ?? []) {
      const parent = reference.identifier?.parent
      if (parent?.type === 'AssignmentExpression' && parent.left === reference.identifier) return parent.right
    }
    return null
  }
}

/**
 * The value a node stands for once the indirection is peeled off: a binding
 * this file initialises, a call that wraps it (`defineComponent(…)`,
 * `withInstall(defineComponent(…))`, `Vue.extend(…)`, an alias of any of them),
 * or an assertion.
 *
 * Calls are followed by their shape, not by name, and that is the loose
 * direction on purpose: an unrecognised builder would make a legal component
 * red, and this rule has no baseline to absorb that. The price is that
 * `export default registerSomething({ … })` also reads what it wraps — a name
 * it would have to spell.
 */
function resolveValue(node, resolve, seen = new Set()) {
  let current = unassert(node)
  while (current) {
    if (current.type === 'Identifier') {
      const next = resolve(current)
      if (!next || seen.has(next)) return undefined
      seen.add(next)
      current = unassert(next)
      continue
    }
    if (current.type === 'CallExpression') {
      current = unassert((current.arguments ?? [])[0])
      continue
    }
    return current
  }
  return undefined
}

/**
 * The object literals that are *this* component's options: the one the default
 * export hands over, and the one `defineOptions` wraps.
 *
 * The default export hands it over plainly (`export default { … }`), through a
 * variable in this file, or through a call that builds a component from it —
 * and any of those may carry an assertion. Nothing else is this component's
 * options: `{ props: […] }` in a component registered under `components:` is
 * another component's, and its props are not names this template can call.
 */
function componentOptions(body, resolve) {
  const statements = Array.isArray(body) ? body : body.body ?? []
  const options = []
  const take = (node) => {
    const value = resolveValue(node, resolve)
    if (value?.type === 'ObjectExpression') options.push(value)
  }
  for (const node of statements) {
    if (node.type === 'ExportDefaultDeclaration') take(node.declaration)
    // `export { options as default }` hands over the same options object.
    if (node.type === 'ExportNamedDeclaration' && !node.source) {
      for (const specifier of node.specifiers ?? []) {
        if (propertyName(specifier.exported) === 'default') take(specifier.local)
      }
    }
  }
  for (const node of walk(body)) {
    if (node.type === 'CallExpression' && node.callee?.type === 'Identifier' && node.callee.name === 'defineOptions') {
      take((node.arguments ?? [])[0])
    }
  }
  return options
}

/** The names an options container declares: a list of names, or an object's keys. */
function containerNames(node, resolve) {
  const value = resolveValue(node, resolve)
  const names = quotedKeys(value)
  if (value?.type === 'ArrayExpression') {
    for (const element of value.elements) {
      if (element?.type === 'Literal' && typeof element.value === 'string') names.push(element.value)
    }
  }
  return names
}

/** The keys of what a `data` or `setup` function hands back. */
function returnedNames(node, resolve) {
  const fn = unassert(node)
  if (fn?.type !== 'FunctionExpression' && fn?.type !== 'ArrowFunctionExpression') return []
  const returned =
    fn.body?.type === 'BlockStatement'
      ? fn.body.body.flatMap((statement) => (statement.type === 'ReturnStatement' ? [statement.argument] : []))
      : [fn.body]
  // `setup() { const vm = { 'open': … }; return vm }` resolves through the
  // binding; a name the function does not declare is the module's, and the
  // scope manager already knows which of the two this read is.
  return returned.flatMap((expression) => quotedKeys(resolveValue(expression, resolve)))
}

const OPTION_CONTAINERS = new Set(['props', 'methods', 'computed', 'inject'])

/** The quoted names an options object declares to its template. */
function collectOptions(options, into, resolve, seen) {
  if (seen.has(options)) return
  seen.add(options)
  for (const property of options.properties) {
    if (property.type !== 'Property') continue
    const name = propertyName(property.key)
    if (name === undefined) continue
    if (OPTION_CONTAINERS.has(name)) {
      for (const declared of containerNames(property.value, resolve)) into.add(declared)
    } else if (name === 'data' || name === 'setup') {
      for (const declared of returnedNames(property.value, resolve)) into.add(declared)
    } else if (name === 'mixins' || name === 'extends') {
      // A mixin's props merge into this component's, so the template can call
      // them. A mixin is often a variable, so resolve each one first.
      const value = resolveValue(property.value, resolve)
      const mixins = value?.type === 'ArrayExpression' ? value.elements : [value]
      for (const mixin of mixins) {
        const resolved = resolveValue(mixin, resolve)
        if (resolved?.type === 'ObjectExpression') collectOptions(resolved, into, resolve, seen)
      }
    }
  }
}

/**
 * The names a string may still stand for, and only where one is declared: the
 * argument of `defineProps`, as a list of names or as an object whose keys are
 * the names; `defineModel('open')`, which compiles to a prop of that name; and
 * the same names in the component's own options object — the quoted keys of its
 * `props`, `methods`, `computed` and `inject`, what `data` or `setup` hands
 * back, and the props of a mixin or an extended component. A container or a
 * mixin written as a variable elsewhere in this file is resolved to it, so
 * `const props = ['open']` counts as the props it is.
 *
 * A string anywhere else is not a declaration. `defineEmits(['open'])` names an
 * event — you cannot call an event — and a label, an i18n key, a key in a
 * lookup table, the source side of a quoted destructuring pattern or another
 * component's props only happen to spell the name. Counting those would hide
 * exactly the call this rule is for.
 */
function stringBindings(body, resolve) {
  const names = new Set()
  for (const node of walk(body)) {
    if (node.type !== 'CallExpression' || node.callee?.type !== 'Identifier') continue
    const [first] = node.arguments ?? []
    if (node.callee.name === 'defineProps') {
      for (const declared of containerNames(first, resolve)) names.add(declared)
    }
    // `defineModel('open')` declares a prop named `open` (and the emit beside
    // it), so the template's `open()` reaches that prop and is bound. The
    // alternative spelling, `defineModel()`, names the model after the
    // variable it is assigned to, which `scriptMentions` already counts.
    if (node.callee.name === 'defineModel') {
      const name = unassert(first)
      if (name?.type === 'Literal' && typeof name.value === 'string') names.add(name.value)
    }
  }
  for (const options of componentOptions(body, resolve)) collectOptions(options, names, resolve, new Set())
  return names
}

/**
 * The names a template binds for itself: `v-for` aliases and `v-slot` props.
 *
 * Collected across the whole template rather than per node, deliberately: the
 * question is only whether a name could be a local somewhere in this file, and
 * answering it loosely can lose a finding but cannot invent one. What it must
 * not miss is the alias a call sits inside —
 * `<li v-for="item in items">…@click="open(item)"` calls the component's
 * `open`, not a local one.
 */
function templateLocals(templateBody) {
  const locals = []
  for (const node of walk(templateBody)) {
    // `left` is a list, and so is `params` under a slot scope: one binding for
    // `v-for="item in rows"`, two for `v-for="(value, key) in entries"`, and
    // the parser hands over a list either way.
    if (node.type === 'VForExpression') {
      for (const pattern of node.left ?? []) patternNames(pattern, locals)
    }
    if (node.type === 'VSlotScopeExpression') {
      for (const pattern of node.params ?? []) patternNames(pattern, locals)
    }
  }
  return locals
}

/** Parse a `.vue` file with the parser the lint config uses, so a file the editor reads is one this can judge. */
function parseComponent(code, file) {
  const parsed = vueParser.parseForESLint(code, {
    sourceType: 'module',
    ecmaVersion: 'latest',
    parser: tsParser,
    parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
    filePath: file,
  })
  // A template whose expressions did not parse is a half-read component, and a
  // half-read component is not a clean one: the parser reports those on the
  // template body rather than throwing (only a broken `<script>` throws), so a
  // rule that read `.templateBody` and nothing else would judge the part it
  // could see and call the file clean.
  //
  // Only the errors carrying no `code`. The template body also collects the
  // compiler's HTML remarks — a `/>` on a non-void element, an attribute with
  // no value — and those are a style question about a file that was read from
  // end to end; the tree has hundreds of them, and failing on them would make
  // this check unable to judge anything at all. The two kinds are told apart by
  // exactly that: a remark about the markup has a code, and an expression the
  // parser could not read has only a message.
  const unparsed = (parsed.ast.templateBody?.errors ?? []).filter((error) => !error.code)
  if (unparsed.length) {
    throw new Error(`template: ${unparsed.map((error) => error.message).join('; ')}`)
  }
  return parsed
}

/**
 * Every bare call in a component that the component binds nothing for.
 *
 * The two halves come from two different analyses, and both are needed:
 * `globalScope.through` holds the references that resolved to no binding in
 * `<script setup>` — the list `no-undef` would read, minus the injected
 * globals — while a template's calls are in the template AST and in no scope
 * at all.
 *
 * @param {string} code the SFC source
 * @param {string} file the component's path, relative to the frontend root
 * @returns {{name: string, line: number, column: number, where: string}[]}
 */
export function unboundCalls(code, file) {
  const { ast, scopeManager } = parseComponent(code, file)
  const resolve = bindingResolver(scopeManager)
  const calls = []
  const seen = new Set()

  const record = (identifier, where) => {
    // The AST counts columns from 0 and lines from 1; a reader pastes this
    // location into a terminal or an editor, both of which count from 1 in both
    // halves. So the column is lifted by one here, and `file:line:column` means
    // the same thing in this report as in the eslint-based one beside it.
    const line = identifier.loc.start.line
    const column = identifier.loc.start.column + 1
    const key = `${where}:${line}:${column}:${identifier.name}`
    if (seen.has(key)) return
    seen.add(key)
    calls.push({ name: identifier.name, line, column, where })
  }

  for (const reference of scopeManager.globalScope?.through ?? []) {
    const identifier = reference.identifier
    const parent = identifier.parent
    if (parent?.type === 'CallExpression' && parent.callee === identifier) record(identifier, SCRIPT)
  }

  const templateBody = ast.templateBody
  if (templateBody) {
    const bound = scriptMentions(ast.body)
    for (const name of templateLocals(templateBody)) bound.add(name)
    for (const name of stringBindings(ast.body, resolve)) bound.add(name)
    for (const node of walk(templateBody)) {
      if (node.type !== 'CallExpression') continue
      const callee = node.callee
      if (callee?.type !== 'Identifier' || bound.has(callee.name)) continue
      record(callee, TEMPLATE)
    }
  }

  return calls.sort((a, b) => a.line - b.line || a.column - b.column)
}

/** The declaration to name in the report: one in the component's own directory, else the first. */
function nearestDefinition(componentFile, definitions) {
  const directory = componentFile.slice(0, componentFile.lastIndexOf('/'))
  return definitions.find((file) => file.slice(0, file.lastIndexOf('/')) === directory) ?? definitions[0]
}

/**
 * The rule, over one component.
 *
 * @param {string} code the component source
 * @param {string} file the component's path, relative to the frontend root
 * @param {Map<string, string[]>} definitions internal name -> modules declaring it
 * @returns {{name: string, file: string, line: number, column: number, where: string, definedIn: string}[]}
 */
export function findGlobalFallbacks(code, file, definitions) {
  const violations = []
  for (const call of unboundCalls(code, file)) {
    const defining = definitions.get(call.name)
    if (!defining?.length) continue
    violations.push({ ...call, file, definedIn: nearestDefinition(file, defining) })
  }
  return violations
}

/** Count violations per component, the shape the ratchet compares. */
export function countByFile(violations) {
  const counts = {}
  for (const { file } of violations) counts[file] = (counts[file] ?? 0) + 1
  return counts
}

/**
 * Read the tree and judge it. The runner is the I/O around this.
 *
 * A file the parser refuses is not a file with no finding: it goes in
 * `unjudged`, and the runner's only honest answer then is "cannot judge". The
 * alternative — letting the throw escape or swallowing it — is a gate that goes
 * green over a component it never read.
 *
 * `sources` and `components` can be narrowed to named paths; that is how the
 * self-test judges a planted pair without paying for 1700 files, and the report
 * says how much it scanned so a narrowed run cannot pass for a full one.
 *
 * @param {{root?: string, sources?: string[], components?: string[]}} [options]
 * @returns {{definitions: Map<string, string[]>, violations: object[], unjudged: {path: string, message: string}[]}}
 */
export function judgeTree({
  root = FRONTEND_ROOT,
  sources = GLOBAL_FALLBACK_SOURCES,
  components = GLOBAL_FALLBACK_FILES,
} = {}) {
  const unjudged = []
  const read = (path) => {
    try {
      return { path, code: readFileSync(join(root, path), 'utf8') }
    } catch (error) {
      unjudged.push({ path, message: error.message })
      return null
    }
  }

  const modules = collectFiles(sources, { root, ignores: GLOBAL_FALLBACK_IGNORES }).map(read).filter(Boolean)
  let definitions = new Map()
  try {
    definitions = collectDefinitions(modules)
  } catch (error) {
    // A module that does not parse may be hiding the declaration the finding is
    // about, so there is no smaller answer than "could not judge".
    unjudged.push({ path: '<modules>', message: error.message })
  }

  const violations = []
  for (const path of collectFiles(components, { root, ignores: GLOBAL_FALLBACK_IGNORES })) {
    const file = read(path)
    if (!file) continue
    try {
      violations.push(...findGlobalFallbacks(file.code, path, definitions))
    } catch (error) {
      unjudged.push({ path, message: error.message })
    }
  }
  return { definitions, violations, unjudged }
}

// NO BASELINE, ON PURPOSE. Every other ratchet in this directory freezes the
// counts its rule found on the day it landed, because as a plain rule it would
// have turned the whole tree red on day one, and a gate that reds everywhere
// gets switched off. This rule was measured before it was written: 0 findings
// over 751 components and 957 modules on 2026-10-16, so there is nothing to
// freeze — and a baseline here would be worse than useless, because its one use
// is to let a finding stand, and the whole subject of this rule is a call that
// fails in silence. So the arithmetic is only the shape the reporter already
// reads: `compare` against an empty baseline, reused from tsc-ratchet-core.mjs
// rather than reimplemented, so `ok`, `regressions` and `currentTotal` mean
// here what they mean in the four gates beside this one.
export { compare } from './tsc-ratchet-core.mjs'

/**
 * The human-readable half: which call, in which component, and what to do.
 *
 * Both halves of a finding are named — the module that kept the name to itself
 * and the component that called it — because neither file is wrong alone: the
 * module may export it, or the component may have meant the global and should
 * say `window.foo()`.
 *
 * @param {{regressions: unknown[], currentTotal: number}} result
 * @param {{file: string, line: number, column: number, where: string, name: string, definedIn: string}[]} [details]
 */
export function formatGlobalFallbackReport(result, details = []) {
  if (!result.regressions.length) return 'No call falls back to a name a module keeps to itself.'
  const lines = ['Bare calls this component binds nothing for, to a name a module keeps to itself:']
  for (const violation of details) {
    lines.push(
      `  ${violation.file}:${violation.line}:${violation.column}  ${violation.name}()  [${violation.where}]` +
        ` — ${violation.definedIn} declares it and never hands it out`
    )
  }
  lines.push('')
  lines.push('Three ways out: export that name from the module and import it here; hand')
  lines.push('it back from the composable this component calls, and take it out of the')
  lines.push('returned object; or, if the call was meant to be a browser global, say so')
  lines.push('as `window.foo()`. Left as it is, the call reaches whatever global shares')
  lines.push('the name, or throws a ReferenceError, and nothing between the two files')
  lines.push('says which.')
  lines.push('The rule and its messages live in scripts/global-fallback-ratchet-core.mjs.')
  lines.push('')
  lines.push(`total: ${result.currentTotal} call(s)`)
  return lines.join('\n')
}
