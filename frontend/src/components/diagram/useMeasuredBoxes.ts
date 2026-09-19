import type { DiagramBox } from '@/lib/diagramGeometry'

import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

/**
 * 量一组节点在画布里的真实位置，供 SVG 连线用。
 *
 * 为什么量真实位置而不是照着数据算：表名、类型串、注释的长短不定，换行与否取决
 * 于实际字体宽度。自己算一套高度，字体一变就连线错位，而且错得不明显。
 *
 * 量完存成普通对象（shallowRef），不给每个节点建响应式对象 —— 观测的是
 * ResizeObserver，不需要 Vue 再帮倒忙。
 *
 * 纯几何（怎么从两个盒子得到一条曲线）在 `@/lib/diagramGeometry`，那里能单测。
 */
export function useMeasuredBoxes(revision: () => unknown) {
  const host = ref<HTMLElement | null>(null)
  const boxes = shallowRef<Record<string, DiagramBox>>({})
  const size = ref({ w: 0, h: 0 })

  const nodes = new Map<string, HTMLElement>()
  let observer: ResizeObserver | null = null
  let frame = 0

  function measure() {
    const root = host.value
    if (!root) return
    const base = root.getBoundingClientRect()
    const next: Record<string, DiagramBox> = {}
    for (const [id, el] of nodes) {
      const rect = el.getBoundingClientRect()
      next[id] = { x: rect.left - base.left, y: rect.top - base.top, w: rect.width, h: rect.height }
    }
    boxes.value = next
    size.value = { w: base.width, h: base.height }
  }

  /** 量尺寸会引起重排，重排又触发 ResizeObserver —— 用 rAF 合并成每帧一次。 */
  function scheduleMeasure() {
    if (frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      measure()
    })
  }

  function observeAll() {
    observer?.disconnect()
    if (host.value) observer?.observe(host.value)
    for (const el of nodes.values()) observer?.observe(el)
  }

  function setNodeRef(id: string, el: unknown) {
    if (el instanceof HTMLElement) nodes.set(id, el)
    else nodes.delete(id)
  }

  onMounted(async () => {
    await nextTick()
    measure()
    // 字体换进来之后行高会变，第一帧量到的位置就不准了。
    if (document.fonts) void document.fonts.ready.then(scheduleMeasure)
    observer = new ResizeObserver(scheduleMeasure)
    observeAll()
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    if (frame) cancelAnimationFrame(frame)
  })

  watch(revision, async () => {
    await nextTick()
    observeAll()
    measure()
  })

  return { host, boxes, size, setNodeRef, remeasure: scheduleMeasure }
}
