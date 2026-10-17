// What the platform's check does after it has taken its pictures: use the app
// the way a reader would. Every control is moved to the ends of its range,
// every option chosen, every button pressed, every point the reader may drag
// taken to the corners it can reach. An app that throws, or puts NaN on
// screen, after any of these is refused the way one that throws on opening
// is — with what was done to it, so its writer can find the case.
//
// The check's browser calls `window.__inspect_exercise()` when it is asked to
// (backend/app/domain/web_snippet/check.py); the page answers how many
// things it did. A browser that does not ask never runs it.
import { optionsOf } from './components/emit'
import { acting, after, fail, failures } from './runtime'

/** At most this many actions, and this long: a check, not a fuzzer. */
const MAX_ACTIONS = 80
const MAX_MS = 8000

type Held = HTMLElement & Record<string, unknown>
type Action = { what: string; run: () => void; settle?: number }

const frame = () => new Promise<void>((done) => requestAnimationFrame(() => done()))
const wait = (ms: number) => new Promise<void>((done) => setTimeout(done, ms))
const num = (v: unknown, fallback: number) =>
  v !== '' && v != null && Number.isFinite(Number(v)) ? Number(v) : fallback
const show = (v: unknown) =>
  Array.isArray(v) ? `(${v.map((c) => show(c)).join(', ')})` : String(Number(Number(v).toPrecision(6)))
const named = (el: Element, fallback: string) => {
  const label = (el as Held).label ?? el.getAttribute('label') ?? el.getAttribute('aria-label')
  const text = label != null && String(label).trim() ? String(label).trim() : fallback
  return `「${text.slice(0, 20)}」`
}

function set(el: Held, value: unknown) {
  el.value = value
  for (const kind of ['input', 'change'])
    el.dispatchEvent(new CustomEvent(kind, { detail: value, bubbles: true, composed: true }))
}

/** What a reader can do to one element, as actions. */
function actionsOf(el: Held): Action[] {
  const tag = el.tagName.toLowerCase()
  if (el.hasAttribute('disabled') || el.disabled === true) return []
  if (tag === 'c-slider' || tag === 'c-number') {
    const [lo, hi] = [num(el.min, 0), num(el.max, 100)]
    // Only values a reader can reach: on the control's steps, counted from
    // its minimum.
    const step = num(el.step, 0)
    const on = (v: number) => (step > 0 ? Number((lo + Math.round((v - lo) / step) * step).toPrecision(12)) : v)
    const who = named(el, tag === 'c-slider' ? '滑块' : '数字输入框')
    return [...new Set([lo, on(hi) > hi ? on(hi - step) : on(hi), on((lo + hi) / 2)])].map((v) => ({
      what: `把${who}设为 ${show(v)}`,
      run: () => set(el, v),
    }))
  }
  if (tag === 'c-select' || tag === 'c-segmented') {
    const who = named(el, '选项')
    return optionsOf(el.options).map((o) => ({ what: `在${who}中选择「${o.label}」`, run: () => set(el, o.value) }))
  }
  if (tag === 'c-switch') {
    const who = named(el, '开关')
    return [!el.value, Boolean(el.value)].map((v) => ({ what: `${v ? '打开' : '关闭'}${who}`, run: () => set(el, v) }))
  }
  if (tag === 'c-player') {
    const count = Math.min(40, Math.max(0, Math.round(num(el.count, 0))))
    return Array.from({ length: count }, (_, i) => ({ what: `播放到第 ${i + 1} 步`, run: () => set(el, i) }))
  }
  if (tag === 'c-point') return pointActions(el)
  if (tag === 'button') {
    const text = (el.textContent ?? '').trim().slice(0, 20) || '按钮'
    // Pressed twice: a play button is pressed again to pause, a step button
    // steps again.
    return [1, 2].map((n) => ({ what: `第 ${n} 次点击「${text}」`, run: () => el.click(), settle: 400 }))
  }
  if (tag === 'input' && ['number', 'range'].includes((el as unknown as HTMLInputElement).type)) {
    const input = el as unknown as HTMLInputElement
    const [lo, hi] = [num(input.min, 0), num(input.max, 100)]
    return [lo, hi].map((v) => ({
      what: `在输入框中填入 ${show(v)}`,
      run: () => {
        input.value = String(v)
        input.dispatchEvent(new Event('input', { bubbles: true }))
        input.dispatchEvent(new Event('change', { bubbles: true }))
      },
    }))
  }
  return []
}

/** A point the reader may drag, taken to the far corners of where it may go. */
function pointActions(el: Held): Action[] {
  const drag = String(el.drag ?? '')
  const on = typeof el.on === 'function' ? (el.on as (...a: number[]) => number) : null
  if (!drag && !on) return []
  const box = el.closest('c-plane, c-space') as Held | null
  if (!box) return []
  const space = box.tagName.toLowerCase() === 'c-space'
  const axes = space ? ['x', 'y', 'z'] : ['x', 'y']
  const range = (a: string): [number, number] => {
    const r = box[a]
    return Array.isArray(r) && r.length >= 2 ? [num(r[0], -1), num(r[1], 1)] : [-1, 1]
  }
  const free = on ? (space ? ['x', 'y'] : ['x']) : axes.filter((a) => drag.includes(a))
  const was = Array.isArray(el.value) ? (el.value as unknown[]).map((v) => num(v, 0)) : axes.map(() => 0)
  const who = named(el, '可拖动的点')
  return [0, 1].map((end) => {
    const at = [...was]
    for (const a of free) at[axes.indexOf(a)] = range(a)[end]
    if (on) at[axes.length - 1] = Number(space ? on(at[0], at[1]) : on(at[0]))
    return { what: `把${who}拖到 ${show(at)}`, run: () => set(el, at) }
  })
}

const READER_CONTROLS = 'c-slider, c-number, c-select, c-segmented, c-switch, c-player, c-point, button, input'

/** Where NaN shows: in text the reader reads, or in where a shape is drawn. */
function nanOnScreen(root: Element): string | null {
  const look = (node: Node): string | null => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent ?? ''
      return /\bNaN\b/.test(text) ? `文字「${text.trim().slice(0, 30)}」` : null
    }
    if (!(node instanceof Element)) return null
    if (node.tagName === 'STYLE' || node.tagName === 'SCRIPT') return null
    for (const attr of node.attributes)
      if (/\bNaN\b/.test(attr.value))
        return `<${node.tagName.toLowerCase()}> 的 ${attr.name}="${attr.value.slice(0, 40)}"`
    for (const child of node.childNodes) {
      const found = look(child)
      if (found) return found
    }
    const shadow = (node as Element).shadowRoot
    return shadow ? look(shadow) : null
  }
  return look(root)
}

export async function exercise(root: HTMLElement): Promise<number> {
  const started = performance.now()
  const done = new Set<Element>()
  let count = 0
  const check = (what: string | null, before: number) => {
    if (failures > before) return false
    const nan = nanOnScreen(root)
    if (!nan) return true
    acting(null)
    fail(
      `${what ? `读者${after(what)}，` : '打开时'}画面上出现了 NaN：${nan}。检查这时的计算，例如除以零、对负数开方或读取不存在的项。`
    )
    return false
  }
  if (!check(null, failures)) return count
  // Controls an action reveals (a panel switched on) are taken in the next pass.
  for (let pass = 0; pass < 3; pass++) {
    const fresh = [...root.querySelectorAll(READER_CONTROLS)].filter((el) => !done.has(el))
    if (!fresh.length) break
    for (const el of fresh) {
      done.add(el)
      for (const action of actionsOf(el as Held)) {
        if (count >= MAX_ACTIONS || performance.now() - started > MAX_MS) return count
        if (!el.isConnected) break
        const before = failures
        acting(action.what)
        try {
          action.run()
        } catch (e) {
          fail(String((e as Error)?.message ?? e))
        }
        count++
        await frame()
        await frame()
        if (action.settle) await wait(action.settle)
        const fine = check(action.what, before)
        acting(null)
        if (!fine) return count
      }
    }
  }
  return count
}
