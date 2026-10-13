// What the shell can tell from an app's source alone, before it runs: a
// control the reader moves whose value has nowhere to go. Such an app draws
// and passes every check that only looks, and the first person to drag or
// type finds that nothing follows — so the shell refuses it at compile time,
// with the line and what to write instead, the way it refuses a broken one.
//
// Only what is certain is refused: a `v-model` on a name the script declares
// as something nobody can write, `:v-model`, and a control nobody listens to.

type Loc = { start: { line: number } }
type Prop = {
  type: number
  name: string
  loc: Loc
  exp?: { content: string }
  arg?: { content: string }
}
type Node = { type: number; tag?: string; props?: Prop[]; children?: Node[]; loc: Loc }
type Statement = {
  type: string
  kind?: string
  declarations?: {
    id: { type: string; name?: string }
    init?: { type: string; callee?: { type: string; name?: string }; arguments?: { type: string }[] } | null
  }[]
}
export type Script = {
  bindings?: Record<string, string>
  imports?: Record<string, { imported: string; source: string }>
  scriptSetupAst?: Statement[]
  content: string
}

const ELEMENT = 1
const ATTRIBUTE = 6
const DIRECTIVE = 7
/** The elements a reader moves, each of which hands the app a new value. */
const CONTROLS = new Set(['c-slider', 'c-select', 'c-segmented', 'c-number', 'c-switch', 'c-player', 'c-board'])
/** Declared so that a `v-model` cannot write it. */
const FIXED = new Set(['literal-const', 'setup-const', 'setup-reactive-const'])
const IDENT = /^[A-Za-z_$][\w$]*$/

/** Names declared as `const x = computed(() => …)`: computed with no setter. */
function readonlyComputeds(script: Script): Set<string> {
  const computed = new Set(
    Object.entries(script.imports ?? {})
      .filter(([, i]) => i.source === 'vue' && i.imported === 'computed')
      .map(([local]) => local)
  )
  const out = new Set<string>()
  for (const s of script.scriptSetupAst ?? []) {
    if (s.type !== 'VariableDeclaration') continue
    for (const d of s.declarations ?? []) {
      const init = d.init
      if (d.id.type !== 'Identifier' || init?.type !== 'CallExpression') continue
      if (init.callee?.type !== 'Identifier' || !computed.has(init.callee.name ?? '')) continue
      if (init.arguments?.[0]?.type !== 'ObjectExpression') out.add(d.id.name ?? '')
    }
  }
  return out
}

const has = (el: Node, name: string) =>
  (el.props ?? []).some(
    (p) =>
      (p.type === ATTRIBUTE && p.name === name) ||
      (p.type === DIRECTIVE && p.name === 'bind' && p.arg?.content === name)
  )
const listens = (el: Node) =>
  (el.props ?? []).some(
    (p) =>
      p.type === DIRECTIVE &&
      (p.name === 'model' || (p.name === 'on' && (!p.arg || p.arg.content === 'input' || p.arg.content === 'change')))
  )

/** The first thing wrong with how the app's controls are wired, or null. */
export function miswired(template: Node | undefined, script: Script | null): string | null {
  if (!template) return null
  const bindings = script?.bindings ?? {}
  const readonly = script ? readonlyComputeds(script) : new Set<string>()
  // Listening by hand (`ref` + addEventListener) is out of sight of the template.
  const byHand = /addEventListener/.test(script?.content ?? '')

  const walk = (node: Node, heard: boolean): string | null => {
    if (node.type !== ELEMENT) return null
    const tag = node.tag ?? ''
    const control = CONTROLS.has(tag) || (tag === 'c-point' && (has(node, 'drag') || has(node, 'on')))
    // A point nobody can drag never writes its v-model back; a page's own
    // <input> does.
    const writes = control || !tag.startsWith('c-')
    for (const p of node.props ?? []) {
      if (p.type !== DIRECTIVE) continue
      if (p.name === 'bind' && p.arg?.content.startsWith('v-'))
        return `第 ${p.loc.start.line} 行：「:${p.arg.content}」不是有效的 Vue 写法，控件不会与变量关联。去掉冒号，写作 ${p.arg.content}="…"。`
      if (p.name !== 'model' || !writes) continue
      const target = p.exp?.content.trim() ?? ''
      if (!IDENT.test(target)) continue
      if (readonly.has(target))
        return `第 ${p.loc.start.line} 行：v-model="${target}" 绑定的是 computed 计算出的值，无法写入：读者操作后它不会改变，画面也不会更新。改为绑定一个 ref，其他量由这个 ref 计算；或者用 :value 加 @input 接收新值。`
      if (FIXED.has(bindings[target] ?? ''))
        return `第 ${p.loc.start.line} 行：v-model="${target}" 绑定的是常量，读者操作时会报错。改为 const ${target} = ref(…)。`
    }
    const hearing = heard || listens(node)
    if (control && !hearing && !byHand)
      return `第 ${node.loc.start.line} 行：读者可以操作这个 <${tag}>，但没有代码接收它的新值（它和外层元素上都没有 v-model、@input 或 @change），操作后画面不会变化。`
    for (const child of node.children ?? []) {
      const found = walk(child, hearing)
      if (found) return found
    }
    return null
  }
  for (const child of template.children ?? []) {
    const found = walk(child, false)
    if (found) return found
  }
  return null
}
