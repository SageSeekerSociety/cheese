// 芝士那一层（`AgentLayer.vue`）上的东西放在哪：量表单上那几格在屏幕上的位置，换成那一层
// 的坐标。
//
// 指针只有一个。芝士在写名称、详情这种要打字的格子时，它是带名字的文字光标，停在字的
// 末尾（输入框量字宽，详情读编辑器末尾的位置）；写选项、数字、日期、列表时，它是箭头，
// 指着那一格。芝士写着的时候每隔一会儿重量一次：字在长，格子也可能被挤下去。
import type { Ref } from 'vue'
import type { AgentBox, AgentMark, AgentPointer } from '@/components/tasks/AgentLayer.vue'

import { onBeforeUnmount, ref, watch } from 'vue'

import { fieldLabel } from '@/lib/assistantView'

/** 打字的格子：光标跟着字走。 */
const TYPED = new Set(['name', 'description'])
const FOLLOW_MS = 120
/** 人自己滚过页面之后这么久不跟着滚。 */
const HANDS_OFF_MS = 2500
/** 光标离屏幕上下边缘至少留这么多。 */
const MARGIN = 72

function scroller(el: HTMLElement): HTMLElement {
  for (let at = el.parentElement; at; at = at.parentElement) {
    const { overflowY } = getComputedStyle(at)
    if ((overflowY === 'auto' || overflowY === 'scroll') && at.scrollHeight > at.clientHeight) return at
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement
}

function smooth(): ScrollBehavior {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

let measurer: CanvasRenderingContext2D | null = null
function textWidth(el: HTMLElement, text: string): number {
  try {
    measurer ??= document.createElement('canvas').getContext('2d')
  } catch {
    measurer = null
  }
  if (!measurer) return 0
  measurer.font = getComputedStyle(el).font
  return measurer.measureText(text).width
}

export interface PointerOptions {
  /** 那一层所在的盒子（表单的外框，`position: relative`）。 */
  host: Ref<HTMLElement | null>
  writing: Ref<string | null>
  marks: Ref<Record<string, number>>
  /** 芝士在答：指针在场。 */
  active: Ref<boolean>
  /** 详情末尾在屏幕上的位置。 */
  descriptionEnd: () => { left: number; top: number; bottom: number } | null
  /** 芝士正在改的详情里那一段（`useTaskDraft` 的 passage）。 */
  passage?: Ref<{ find: string; replace: string | null } | null>
  /** 芝士停在一格前面、还没开始写：原来有字就先整格选中。 */
  holding?: Ref<boolean>
}

/** Markdown 写的一段在页面上读出来的字：去掉标记符号，空白并成一个。 */
function shownText(md: string): string {
  return md
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/[*_`~]/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

/** `root` 里头一处读出来是 `text` 的地方（跨过几个文字节点也找得到）；找不到是 null。 */
function findText(root: HTMLElement, text: string, after?: Range | null): Range | null {
  if (!text) return null
  const nodes: Text[] = []
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let n = walk.nextNode(); n; n = walk.nextNode()) nodes.push(n as Text)
  // 把文字节点首尾相接，空白并成一个，记着每个字在哪个节点的第几位。
  let joined = ''
  const at: [Text, number][] = []
  let space = false
  for (const node of nodes) {
    for (let i = 0; i < node.data.length; i++) {
      const c = node.data[i]
      if (/\s/.test(c)) {
        if (space) continue
        space = true
        joined += ' '
      } else {
        space = false
        joined += c
      }
      at.push([node, i])
    }
  }
  let from = 0
  if (after) {
    const start = at.findIndex(([node, i]) => node === after.startContainer && i >= after.startOffset)
    if (start > 0) from = start
  }
  const index = joined.indexOf(text, from)
  if (index < 0) return null
  const [startNode, startOffset] = at[index]
  const [endNode, endOffset] = at[index + text.length - 1]
  const range = document.createRange()
  range.setStart(startNode, startOffset)
  range.setEnd(endNode, endOffset + 1)
  return range
}

export function useAgentPointer(options: PointerOptions) {
  const pointer = ref<AgentPointer | null>(null)
  const outline = ref<AgentBox | null>(null)
  const marks = ref<AgentMark[]>([])
  /** 芝士选中、要换掉的那段字。 */
  const selection = ref<AgentBox[]>([])
  /** 改的那一段原文在哪（新文字从这里往后找）。 */
  let anchor: Range | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  /** 最后指着的地方：一步做完、下一步还没开始时，指针停在那里，不消失。 */
  let rest: AgentPointer | null = null
  /** 人最近一次自己滚页面的时间。 */
  let handled = 0
  /** 已经滚到过的那一格：每格只在换到它时滚一次。 */
  let shownKey: string | null = null

  /** 把屏幕上 [top, bottom] 这一段滚进 el 所在滚动区的视野。 */
  function reveal(el: HTMLElement, top: number, bottom: number) {
    if (Date.now() - handled < HANDS_OFF_MS) return
    const box = scroller(el)
    const view =
      box === document.scrollingElement || box === document.documentElement
        ? { top: 0, bottom: window.innerHeight }
        : box.getBoundingClientRect()
    let by = 0
    if (bottom > view.bottom - MARGIN) by = bottom - (view.bottom - MARGIN)
    if (top - by < view.top + MARGIN) by = top - (view.top + MARGIN)
    if (Math.abs(by) > 1) box.scrollBy?.({ top: by, behavior: smooth() })
  }

  function field(key: string): HTMLElement | null {
    return options.host.value?.querySelector<HTMLElement>(`[data-agent-field="${key}"]`) ?? null
  }

  function measure() {
    const host = options.host.value
    if (!host) return
    const origin = host.getBoundingClientRect()
    const box = (r: DOMRect | { left: number; top: number; right: number; bottom: number }) => ({
      x: r.left - origin.left,
      y: r.top - origin.top,
      w: r.right - r.left,
      h: r.bottom - r.top,
    })

    marks.value = Object.keys(options.marks.value).flatMap((key) => {
      const el = field(key)
      if (!el) return []
      const r = box(el.getBoundingClientRect())
      return [{ key, label: fieldLabel(key), x: r.x + r.w, y: r.y }]
    })

    const key = options.writing.value
    const el = key ? field(key) : null
    selection.value = []
    if (!key || !el) {
      shownKey = null
      anchor = null
      outline.value = null
      pointer.value = options.active.value ? rest : null
      return
    }
    const control =
      el.querySelector<HTMLElement>('.base-field__control') ?? el.querySelector<HTMLElement>('.v-input') ?? el
    const rect = control.getBoundingClientRect()
    const area = box(rect)
    outline.value = { x: area.x - 4, y: area.y - 4, w: area.w + 8, h: area.h + 8 }
    if (key !== shownKey) {
      shownKey = key
      // 一格比屏幕还高（详情）时先露出它的开头。
      reveal(control, rect.top, Math.min(rect.bottom, rect.top + window.innerHeight / 2))
    }

    if (TYPED.has(key)) {
      const editor = el.querySelector<HTMLElement>('.ProseMirror')
      const passage = options.passage?.value
      if (key === 'description' && editor && passage) {
        // 改详情里的一段：先选中原文，光标在它前面；开始写了，光标跟着新文字走。
        if (passage.replace === null) {
          anchor = findText(editor, shownText(passage.find))
          if (anchor) {
            selection.value = [...anchor.getClientRects()].map((r) => box(r))
            const r = anchor.getClientRects()[0]
            reveal(control, r.top, r.bottom)
            pointer.value = { x: r.left - origin.left, y: r.top - origin.top, mode: 'caret', height: r.height }
            rest = pointer.value
            return
          }
        } else {
          const typed = findText(editor, shownText(passage.replace), anchor)
          if (typed) {
            const rects = typed.getClientRects()
            const r = rects[rects.length - 1]
            reveal(control, r.top, r.bottom)
            pointer.value = { x: r.right - origin.left, y: r.top - origin.top, mode: 'caret', height: r.height }
            rest = pointer.value
            return
          }
        }
      }
      if (options.holding?.value) {
        // 要整格重写、原来有字：先把原来的字选中一下。
        const input = el.querySelector<HTMLInputElement>('input')
        if (input?.value) {
          const r = input.getBoundingClientRect()
          const pad = parseFloat(getComputedStyle(input).paddingLeft) || 0
          const line = parseFloat(getComputedStyle(input).lineHeight) || 20
          const width = Math.min(textWidth(input, input.value), r.width - pad * 2)
          selection.value = [
            { x: r.left - origin.left + pad, y: r.top - origin.top + (r.height - line) / 2, w: width, h: line },
          ]
        } else if (editor?.textContent?.trim()) {
          const all = document.createRange()
          all.selectNodeContents(editor)
          selection.value = [...all.getClientRects()].slice(0, 40).map((r) => box(r))
        }
      }
      if (key === 'description') {
        const end = options.descriptionEnd()
        if (end) {
          reveal(control, end.top, end.bottom)
          pointer.value = {
            x: end.left - origin.left,
            y: end.top - origin.top,
            mode: 'caret',
            height: end.bottom - end.top,
          }
          rest = pointer.value
          return
        }
      }
      const input = el.querySelector<HTMLInputElement>('input')
      if (input) {
        const r = input.getBoundingClientRect()
        const pad = parseFloat(getComputedStyle(input).paddingLeft) || 0
        const line = parseFloat(getComputedStyle(input).lineHeight) || 20
        pointer.value = {
          x: r.left - origin.left + pad + Math.min(textWidth(input, input.value), r.width - pad * 2),
          y: r.top - origin.top + (r.height - line) / 2,
          mode: 'caret',
          height: line,
        }
        rest = pointer.value
        return
      }
    }
    pointer.value = { x: area.x + Math.min(area.w * 0.3, 120), y: area.y + area.h / 2, mode: 'arrow' }
    rest = pointer.value
  }

  function follow(on: boolean) {
    if (on && !timer) timer = setInterval(measure, FOLLOW_MS)
    if (!on && timer) {
      clearInterval(timer)
      timer = null
    }
  }

  watch(
    () => [
      options.writing.value,
      options.active.value,
      Object.keys(options.marks.value).join(),
      options.holding?.value,
      options.passage?.value?.replace === null,
    ],
    () => {
      if (!options.active.value) rest = null
      follow(options.active.value)
      requestAnimationFrame(measure)
    },
    { immediate: true }
  )

  const onResize = () => requestAnimationFrame(measure)
  const onHands = () => {
    handled = Date.now()
  }
  const onKey = (event: KeyboardEvent) => {
    if (['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' '].includes(event.key)) onHands()
  }
  window.addEventListener('resize', onResize)
  window.addEventListener('wheel', onHands, { passive: true })
  window.addEventListener('touchmove', onHands, { passive: true })
  window.addEventListener('keydown', onKey)
  onBeforeUnmount(() => {
    follow(false)
    window.removeEventListener('resize', onResize)
    window.removeEventListener('wheel', onHands)
    window.removeEventListener('touchmove', onHands)
    window.removeEventListener('keydown', onKey)
  })

  /** 把一格滚进视野，并描一下。 */
  function jump(key: string) {
    const el = field(key)
    if (!el) return
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    const host = options.host.value
    if (!host) return
    setTimeout(() => {
      const origin = host.getBoundingClientRect()
      const r = el.getBoundingClientRect()
      outline.value = { x: r.left - origin.left - 4, y: r.top - origin.top - 4, w: r.width + 8, h: r.height + 8 }
      setTimeout(() => {
        if (!options.writing.value) outline.value = null
      }, 1200)
    }, 350)
  }

  return { pointer, outline, marks, selection, measure, jump }
}
