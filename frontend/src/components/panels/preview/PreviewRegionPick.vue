<script setup lang="ts">
// 应用那一档的圈选：`cheese serve` 起的应用没有桥，宿主读不到它的 DOM，只能在 iframe 上
// 盖一层透明遮罩让读者拖出一个矩形。坐标相对遮罩（它贴满 iframe 那块地方），视口就是遮罩
// 自己的尺寸。有桥的网页不用它（帧自己描边）。
import type { WebRect, WebViewport } from '@/lib/quotedContext'

import { computed, ref } from 'vue'

const emit = defineEmits<{
  /** 拖出了一块：几何和当时的视口。太小的一下（多半是点了一下）不报。 */
  (e: 'pick', payload: { rect: WebRect; viewport: WebViewport }): void
}>()

const root = ref<HTMLElement | null>(null)
const rect = ref<WebRect | null>(null)
let start: { x: number; y: number } | null = null

function point(event: PointerEvent): { x: number; y: number } | null {
  const box = root.value?.getBoundingClientRect()
  if (!box) return null
  return { x: event.clientX - box.left, y: event.clientY - box.top }
}
function span(a: { x: number; y: number }, b: { x: number; y: number }): WebRect {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) }
}
const boxStyle = computed((): Record<string, string> => {
  const r = rect.value
  if (!r) return {}
  return { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` }
})

function begin(event: PointerEvent) {
  event.preventDefault()
  start = point(event)
  if (!start) return
  rect.value = { x: start.x, y: start.y, w: 0, h: 0 }
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
}
function grow(event: PointerEvent) {
  const here = start && point(event)
  if (start && here) rect.value = span(start, here)
}
function reset() {
  start = null
  rect.value = null
}
function end(event: PointerEvent) {
  const from = start
  const here = point(event)
  reset()
  if (!from || !here) return
  const picked = span(from, here)
  if (picked.w < 4 || picked.h < 4) return
  const box = root.value?.getBoundingClientRect()
  emit('pick', { rect: picked, viewport: { w: Math.round(box?.width ?? 0), h: Math.round(box?.height ?? 0) } })
}
</script>

<template>
  <div
    ref="root"
    class="pick-region"
    data-testid="preview-region-pick"
    @pointerdown="begin"
    @pointermove="grow"
    @pointerup="end"
    @pointercancel="reset"
  >
    <div v-if="rect" class="pick-region__box" :style="boxStyle" />
  </div>
</template>

<style scoped>
.pick-region {
  position: absolute;
  inset: 0;
  z-index: 3;
  cursor: crosshair;
  touch-action: none;
}
.pick-region__box {
  position: absolute;
  border: 1px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}
</style>
