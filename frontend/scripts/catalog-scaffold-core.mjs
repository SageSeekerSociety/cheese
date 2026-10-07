// The pure half of `catalog-scaffold.mjs`: from one SFC's source, the skeleton
// of its `/demo/catalog` entry.
//
// WHY A SKELETON AND NOT AN ENTRY. A catalog card is only worth looking at when
// its props are the product's real shapes (a real room's messages, a real
// task's checks), and no reader of a type can invent those. What a type CAN
// give is the part people get wrong by hand: which props are required, what
// kind of value each takes, which plugins the component reaches for. So the
// scaffold writes that part and leaves every sentence a person has to write as
// `TODO(catalog)` — and `catalog.spec.ts` fails on any entry still carrying
// the marker, so an unfinished skeleton cannot be committed as a card.
//
// The placeholder ARGS are marked too: a value of the right kind is still an
// invented one, and a skeleton whose sentences were filled in but whose args
// were not is exactly as unfinished. A string placeholder is the marker itself;
// a value that cannot hold a string (number, boolean, null, array, object,
// function) is wrapped in `todo(...)` from `src/views/demo/catalogTodo.ts`,
// which returns it unchanged and records the call — the spec fails on that too.
//
// WHERE THE PROPS COME FROM. Vue's own compiler (`compileScript`), the same
// one the build runs: a type-only `defineProps<Props>()` is resolved to the
// runtime declaration the component will actually get, imported types
// included. Reading `defineProps` with a regex would see the type's name and
// nothing else.

import { compileScript, parse, registerTS } from 'vue/compiler-sfc'
import ts from 'typescript'

registerTS(() => ts)

/** The marker every hand-written sentence starts as; the catalog spec rejects it. */
export const TODO = 'TODO(catalog)'

/** Where `todo(...)` lives, as the catalog shards import it (they sit next to it). */
export const TODO_IMPORT = "import { todo } from './catalogTodo'"

/**
 * `{ name, types, required }` for each declared prop, in declaration order.
 *
 * `types` are the runtime constructors the compiler emitted (`String`, `Array`,
 * `null`...); an empty list means "any". `fs` is how imported prop types are
 * read; tests pass an in-memory one.
 */
export function propsOf(source, filename, fs) {
  const { descriptor, errors } = parse(source, { filename })
  if (errors.length) throw new Error(`${filename}: ${errors[0].message}`)
  if (!descriptor.script && !descriptor.scriptSetup) return []
  const out = compileScript(descriptor, { id: 'catalog-scaffold', fs })
  const block = propsBlock(out.content)
  return block ? parseProps(block) : []
}

/** The text between the braces of the component options' `props: { ... }`. */
function propsBlock(code) {
  const start = /\bprops:\s*(?:\/\*[\s\S]*?\*\/\s*)?(?:_mergeDefaults\(\s*)?\{/.exec(code)
  if (!start) return null
  let depth = 1
  let i = start.index + start[0].length
  const from = i
  for (; i < code.length && depth > 0; i++) {
    if (code[i] === '{') depth++
    else if (code[i] === '}') depth--
  }
  return code.slice(from, i - 1)
}

/** Split the props object at its top-level commas and read each member. */
function parseProps(block) {
  const members = []
  let depth = 0
  let cur = ''
  for (const ch of block) {
    if ('{[('.includes(ch)) depth++
    if ('}])'.includes(ch)) depth--
    if (ch === ',' && depth === 0) {
      members.push(cur)
      cur = ''
    } else cur += ch
  }
  if (cur.trim()) members.push(cur)
  const props = []
  for (const raw of members) {
    const member = /^\s*["']?([\w$-]+)["']?\s*:\s*([\s\S]*)$/.exec(raw)
    if (!member) continue
    const [, name, body] = member
    const typeText = /\btype:\s*(\[[^\]]*\]|[\w$]+)/.exec(body)?.[1] ?? (body.trim().startsWith('{') ? '' : body.trim())
    const types = typeText
      .replace(/[[\]]/g, '')
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)
    props.push({ name, types, required: /\brequired:\s*true\b/.test(body) })
  }
  return props
}

/**
 * A value of the right kind, written as TypeScript source, and marked as a
 * placeholder: a string is the marker itself, anything else is `todo(value)`.
 * A prop that may be null gets null — the one value of its type that needs no
 * invention, but still a choice the person has to make, so it is marked too.
 */
export function placeholder(prop) {
  if (prop.types.includes('null')) return 'todo(null)'
  const kinds = prop.types.filter((t) => t !== 'null')
  switch (kinds[0]) {
    case 'String':
      return `'${TODO}'`
    case 'Number':
      return 'todo(0)'
    case 'Boolean':
      return 'todo(false)'
    case 'Array':
      return 'todo([])'
    case 'Function':
      return 'todo(() => {})'
    case 'Object':
      return 'todo({})'
    default:
      return 'todo(undefined)'
  }
}

/** The plugins the component visibly reaches for; the spec proves the guess. */
export function guessNeeds(source) {
  const needs = []
  if (/<v-[a-z]|\bfrom ['"]vuetify/.test(source)) needs.push('vuetify')
  if (/\buseI18n\b|\$t\(|\bfrom ['"]@\/i18n['"]/.test(source)) needs.push('i18n')
  return needs
}

/** `src/components/panels/PanelThreads.vue` -> `panel-threads`. */
export function idOf(file) {
  const base = file
    .split('/')
    .pop()
    .replace(/\.vue$/, '')
  return base
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1-$2')
    .toLowerCase()
}

/** `proto-shell` -> `ProtoShell`, `404` -> `404` (callers prefix what still starts with a digit). */
function pascal(text) {
  return text
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join('')
}

/**
 * A binding name per file, unique across the batch and a legal identifier.
 *
 * The file name is the component's own name, so it is the first choice. Two
 * files of one name (`recover/password/StartView.vue`, `signup/StartView.vue`)
 * take parent directories in front until they differ, and a name that is not
 * an identifier (`404.vue`, `proto-shell.vue`) is PascalCased and, if it still
 * starts with a digit, gets its directory in front too.
 */
export function bindingNames(files) {
  const parts = files.map((file) => file.replace(/\.vue$/, '').split('/'))
  const depth = files.map(() => 1)
  const nameAt = (i) => {
    const name = pascal(parts[i].slice(-depth[i]).join('-'))
    return /^[0-9]/.test(name) ? pascal(parts[i].slice(-depth[i] - 1).join('-')) : name
  }
  for (;;) {
    const names = files.map((_, i) => nameAt(i))
    const clash = names.map((name, i) => names.indexOf(name) !== i || names.lastIndexOf(name) !== i)
    const grow = clash.map((c, i) => c && depth[i] < parts[i].length)
    if (!grow.some(Boolean)) return names
    grow.forEach((g, i) => g && depth[i]++)
  }
}

/**
 * One entry's import line and object literal, as TypeScript source.
 * `usesTodo` says whether the entry calls `todo(...)`, i.e. whether the file it
 * is pasted into needs `TODO_IMPORT`.
 */
export function scaffold({ file, source, fs, name = bindingNames([file])[0] }) {
  const props = propsOf(source, file, fs)
  const required = props.filter((p) => p.required)
  const optional = props.filter((p) => !p.required)
  const args = required.map((p) => `      ${p.name}: ${placeholder(p)},`)
  const lines = [
    '  {',
    `    id: '${idOf(`${name}.vue`)}',`,
    `    title: '${name}',`,
    `    about: '${TODO}: 一句话：它是什么、用在哪。',`,
    `    file: '${file}',`,
    `    component: ${name},`,
    `    needs: [${guessNeeds(source)
      .map((n) => `'${n}'`)
      .join(', ')}],`,
    '    args: {',
    ...args,
    ...(optional.length ? [`      // optional: ${optional.map((p) => p.name).join(', ')}`] : []),
    '    },',
    '    states: [',
    `      { name: '${TODO}', note: '${TODO}: 这一格在讲什么。', props: {} },`,
    '    ],',
    '  },',
  ]
  return {
    importLine: `import ${name} from '@/${file.replace(/^src\//, '')}'`,
    entry: lines.join('\n'),
    usesTodo: args.some((line) => line.includes('todo(')),
  }
}
