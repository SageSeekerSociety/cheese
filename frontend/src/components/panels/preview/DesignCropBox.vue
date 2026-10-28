<script setup lang="ts">
// 面板里的裁剪框：铺在**转好、调好之后**看到的那张图上，框外压暗。框里拖是挪，拖四个角是
// 改大小，框外拖是重新框一块。
//
// 和对话浮层里的那份是同一套做法（`lightbox/LightboxCrop.vue`），只是量的东西不同：那边量
// 的是浮动在整个窗口上的一张图，这边量的是面板里那一格。两处的位置都按图的比例记（0–1），
// 所以缩放、换窗口大小、甚至转 90° 都框着同一块。
import type { CropBox } from '@/lib/imageEdit'

import { computed, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 图在面板里占的那块（像素）；裁剪框铺满它。 */
  width: number
  height: number
  modelValue: CropBox
}>()
const emit = defineEmits<{ 'update:modelValue': [box: CropBox] }>()

/** 框至少这么大（占图宽高的比例）：太小了点不中，也裁不出东西。 */
const MIN = 0.04

type Corner = 'nw' | 'ne' | 'sw' | 'se'
type Gesture =
  | { kind: 'move'; from: { x: number; y: number }; box: CropBox }
  | { kind: 'resize'; corner: Corner; box: CropBox }
  | { kind: 'draw'; origin: { x: number; y: number } }

const layer = ref<HTMLElement | null>(null)
let gesture: Gesture | null = null

const style = computed(() => ({
  left: '0px',
  top: '0px',
  width: `${props.width}px`,
  height: `${props.height}px`,
}))
const boxStyle = computed(() => ({
  left: `${props.modelValue.x * 100}%`,
  top: `${props.modelValue.y * 100}%`,
  width: `${props.modelValue.w * 100}%`,
  height: `${props.modelValue.h * 100}%`,
}))

function at(event: PointerEvent) {
  const rect = layer.value!.getBoundingClientRect()
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  }
}
function span(a: { x: number; y: number }, b: { x: number; y: number }): CropBox {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) }
}

function onDown(event: PointerEvent) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  event.stopPropagation()
  event.preventDefault()
  layer.value?.setPointerCapture?.(event.pointerId)
  const corner = (event.target as HTMLElement).dataset.corner as Corner | undefined
  const p = at(event)
  const box = props.modelValue
  if (corner) gesture = { kind: 'resize', corner, box: { ...box } }
  else if (p.x >= box.x && p.x <= box.x + box.w && p.y >= box.y && p.y <= box.y + box.h)
    gesture = { kind: 'move', from: p, box: { ...box } }
  else gesture = { kind: 'draw', origin: p }
}

function onMove(event: PointerEvent) {
  if (!gesture) return
  event.stopPropagation()
  const p = at(event)
  if (gesture.kind === 'move') {
    const { box, from } = gesture
    emit('update:modelValue', {
      ...box,
      x: Math.min(1 - box.w, Math.max(0, box.x + p.x - from.x)),
      y: Math.min(1 - box.h, Math.max(0, box.y + p.y - from.y)),
    })
  } else if (gesture.kind === 'resize') {
    const { box, corner } = gesture
    // 拖的是哪个角，对角那个角就钉在原地。
    const fixed = {
      x: corner.includes('w') ? box.x + box.w : box.x,
      y: corner.includes('n') ? box.y + box.h : box.y,
    }
    const next = span(fixed, p)
    if (next.w >= MIN && next.h >= MIN) emit('update:modelValue', next)
  } else {
    const next = span(gesture.origin, p)
    if (next.w >= MIN && next.h >= MIN) emit('update:modelValue', next)
  }
}

function onUp(event: PointerEvent) {
  if (gesture) event.stopPropagation()
  gesture = null
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag surface; the toolbar's 取消/裁剪 are the keyboard path -->
  <div
    ref="layer"
    class="crop"
    :style="style"
    data-testid="design-crop"
    @pointerdown="onDown"
    @pointermove="onMove"
    @pointerup="onUp"
    @pointercancel="onUp"
    @click.stop
  >
    <div class="crop__box" :style="boxStyle" :aria-label="t('design.cropBox')">
      <span
        v-for="corner in ['nw', 'ne', 'sw', 'se']"
        :key="corner"
        class="crop__handle"
        :class="`crop__handle--${corner}`"
        :data-corner="corner"
      />
    </div>
  </div>
</template>

<style scoped>
.crop {
  position: absolute;
  overflow: hidden;
  cursor: crosshair;
  touch-action: none;
}
/* 框外压暗：一圈很宽的阴影，被外层裁在图的范围里。 */
.crop__box {
  position: absolute;
  border: 1px solid var(--crop-ink);
  box-shadow: 0 0 0 9999px var(--crop-dim);
  cursor: move;
}
.crop__handle {
  position: absolute;
  width: 14px;
  height: 14px;
  background: var(--crop-ink);
  border-radius: var(--radius-pill);
}
.crop__handle--nw {
  top: -7px;
  left: -7px;
  cursor: nwse-resize;
}
.crop__handle--ne {
  top: -7px;
  right: -7px;
  cursor: nesw-resize;
}
.crop__handle--sw {
  bottom: -7px;
  left: -7px;
  cursor: nesw-resize;
}
.crop__handle--se {
  right: -7px;
  bottom: -7px;
  cursor: nwse-resize;
}
</style>
