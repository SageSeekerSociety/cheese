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
}

export function useAgentPointer(options: PointerOptions) {
  const pointer = ref<AgentPointer | null>(null)
  const outline = ref<AgentBox | null>(null)
  const marks = ref<AgentMark[]>([])
  let timer: ReturnType<typeof setInterval> | null = null
  /** 最后指着的地方：一步做完、下一步还没开始时，指针停在那里，不消失。 */
  let rest: AgentPointer | null = null

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
    if (!key || !el) {
      outline.value = null
      pointer.value = options.active.value ? rest : null
      return
    }
    const control =
      el.querySelector<HTMLElement>('.base-field__control') ?? el.querySelector<HTMLElement>('.v-input') ?? el
    const area = box(control.getBoundingClientRect())
    outline.value = { x: area.x - 4, y: area.y - 4, w: area.w + 8, h: area.h + 8 }

    if (TYPED.has(key)) {
      if (key === 'description') {
        const end = options.descriptionEnd()
        if (end) {
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
    () => [options.writing.value, options.active.value, Object.keys(options.marks.value).join()],
    () => {
      if (!options.active.value) rest = null
      follow(options.active.value)
      requestAnimationFrame(measure)
    },
    { immediate: true }
  )

  const onResize = () => requestAnimationFrame(measure)
  window.addEventListener('resize', onResize)
  onBeforeUnmount(() => {
    follow(false)
    window.removeEventListener('resize', onResize)
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

  return { pointer, outline, marks, measure, jump }
}
