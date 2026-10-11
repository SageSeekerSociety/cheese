<script setup lang="ts">
// 一张图放大看：铺满窗口的一层，滚轮或双指缩放、按住拖动，点空白或 Esc 关掉。
//
// 为什么不在新标签页里打开：这张图的字节是这一页 fetch 回来、转成 object URL 的，
// 地址只活在当前这份文档里 —— 新标签页打开它是一片空白（真实附件那个地址只认
// Authorization 头，浏览器自己发的请求带不上，见 AttachmentImage）。
//
// 这一层不取字节、也不还字节：地址由调用方给（`src`），调用方负责在预览关掉之前
// 不 revoke 它（见 AttachmentImage 的 release）。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 图片的 object URL：调用方取好字节转出来的那一个。 */
  src: string
  /** 读屏和取不到图时说的那个名字，通常是文件名。 */
  alt: string
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

/** 一倍是「整张图看得见」，往上放大到原文的八倍。 */
const MAX_SCALE = 8
/** 按下和抬起之间挪过这么多像素，就算拖动，不再当成点了一下空白。 */
const CLICK_SLOP = 4

const stage = ref<HTMLElement | null>(null)
const figure = ref<HTMLImageElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)

const scale = ref(1)
const offset = ref({ x: 0, y: 0 })
/** 一倍下这张图摆多大：拖动的边界按它算。字节到之前是 0。 */
const fitted = ref({ w: 0, h: 0 })
const moving = ref(false)

/** 此刻按着的每一根手指/鼠标。一根是拖，两根是捏。 */
const pointers = new Map<number, { x: number; y: number }>()
type Drag = { from: { x: number; y: number }; offset: { x: number; y: number } }
type Pinch = { distance: number; mid: { x: number; y: number }; scale: number; offset: { x: number; y: number } }
let drag: Drag | null = null
let pinch: Pinch | null = null
let travelled = 0

const style = computed(() => ({
  width: fitted.value.w ? `${fitted.value.w}px` : undefined,
  height: fitted.value.h ? `${fitted.value.h}px` : undefined,
  transform: `translate(${offset.value.x}px, ${offset.value.y}px) scale(${scale.value})`,
}))

/** 图片的显示尺寸：装得下就按原尺寸，装不下就等比缩到窗口里（留一圈边）。 */
function fit() {
  const el = figure.value
  const box = stage.value
  if (!el || !box || !el.naturalWidth || !el.naturalHeight) return
  const room = { w: Math.max(box.clientWidth - 48, 1), h: Math.max(box.clientHeight - 48, 1) }
  const ratio = Math.min(room.w / el.naturalWidth, room.h / el.naturalHeight, 1)
  fitted.value = { w: Math.round(el.naturalWidth * ratio), h: Math.round(el.naturalHeight * ratio) }
}

/** 拖不出界：放大出来的那一圈之外，图至少还有一边贴着窗口。 */
function clamp() {
  const box = stage.value
  if (!box) return
  if (scale.value === 1) {
    offset.value = { x: 0, y: 0 }
    return
  }
  const maxX = Math.max(0, (fitted.value.w * scale.value - box.clientWidth) / 2)
  const maxY = Math.max(0, (fitted.value.h * scale.value - box.clientHeight) / 2)
  offset.value = {
    x: Math.min(maxX, Math.max(-maxX, offset.value.x)),
    y: Math.min(maxY, Math.max(-maxY, offset.value.y)),
  }
}

/** 缩放，并且让 (ox, oy) 这一点（相对窗口中心的像素）在缩放前后停在原地。 */
function zoomTo(next: number, ox = 0, oy = 0) {
  const from = scale.value
  const to = Math.min(MAX_SCALE, Math.max(1, next))
  if (to === from) return
  offset.value = {
    x: ox - ((ox - offset.value.x) / from) * to,
    y: oy - ((oy - offset.value.y) / from) * to,
  }
  scale.value = to
  clamp()
}

function centre() {
  const box = stage.value
  if (!box) return { x: 0, y: 0 }
  const rect = box.getBoundingClientRect()
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
}

function reset() {
  zoomTo(1)
  offset.value = { x: 0, y: 0 }
}

function onWheel(event: WheelEvent) {
  event.preventDefault()
  const middle = centre()
  zoomTo(scale.value * Math.exp(-event.deltaY * 0.0015), event.clientX - middle.x, event.clientY - middle.y)
}

function onPointerDown(event: PointerEvent) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  travelled = 0
  moving.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
  if (pointers.size === 1) {
    pinch = null
    drag = { from: { x: event.clientX, y: event.clientY }, offset: { ...offset.value } }
  } else if (pointers.size === 2) {
    drag = null
    const [a, b] = [...pointers.values()]
    pinch = {
      distance: Math.hypot(a.x - b.x, a.y - b.y),
      mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      scale: scale.value,
      offset: { ...offset.value },
    }
  }
}

function onPointerMove(event: PointerEvent) {
  const previous = pointers.get(event.pointerId)
  if (!previous) return
  travelled += Math.hypot(event.clientX - previous.x, event.clientY - previous.y)
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })

  if (pointers.size >= 2 && pinch) {
    const [a, b] = [...pointers.values()]
    const distance = Math.hypot(a.x - b.x, a.y - b.y)
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    const middle = centre()
    // 两指中点挪了多少就跟着挪多少（这是拖动），两指之间张开了几倍就放大几倍。
    offset.value = {
      x: pinch.offset.x + (mid.x - pinch.mid.x),
      y: pinch.offset.y + (mid.y - pinch.mid.y),
    }
    zoomTo(pinch.scale * (pinch.distance ? distance / pinch.distance : 1), mid.x - middle.x, mid.y - middle.y)
    return
  }

  if (pointers.size === 1 && drag) {
    offset.value = {
      x: drag.offset.x + (event.clientX - drag.from.x),
      y: drag.offset.y + (event.clientY - drag.from.y),
    }
    clamp()
  }
}

function onPointerUp(event: PointerEvent) {
  pointers.delete(event.pointerId)
  if (pointers.size === 1) {
    // 从捏变回一根手指：接着拖，别跳。
    const [only] = [...pointers.values()]
    pinch = null
    drag = { from: { x: only.x, y: only.y }, offset: { ...offset.value } }
  } else if (pointers.size === 0) {
    pinch = null
    drag = null
    moving.value = false
  }
}

/** 点空白关掉。拖动过的那一下不算点：那是想看细节，不是想走。 */
function onStageClick(event: MouseEvent) {
  if (event.target !== stage.value || travelled > CLICK_SLOP) return
  emit('close')
}

function onDoubleClick(event: MouseEvent) {
  const middle = centre()
  if (scale.value > 1) reset()
  else zoomTo(2.5, event.clientX - middle.x, event.clientY - middle.y)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  closeButton.value?.focus()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div
      ref="stage"
      class="lightbox"
      role="dialog"
      aria-modal="true"
      :aria-label="alt"
      @click="onStageClick"
      @wheel="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
    >
      <img
        ref="figure"
        class="lightbox__figure"
        :class="{ 'lightbox__figure--moving': moving, 'lightbox__figure--zoomed': scale > 1 }"
        :src="props.src"
        :alt="alt"
        :title="t('work.image.zoomHint')"
        :style="style"
        draggable="false"
        @load="fit"
        @dblclick="onDoubleClick"
      />
      <button
        ref="closeButton"
        type="button"
        class="lightbox__close"
        :aria-label="t('work.image.close')"
        @click="emit('close')"
      >
        <v-icon size="18">mdi-close</v-icon>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
/* 铺满窗口的一层，图和窗口一样大：缩放和拖动都发生在这块面上，所以这里不吃
   手势 —— 触屏上滚页面、双指缩放浏览器，都不是在这个时刻想做的事。 */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: var(--overlay);
  touch-action: none;
}
.lightbox__figure {
  max-width: 100%;
  max-height: 100%;
  user-select: none;
  -webkit-user-drag: none;
  transition: transform var(--dur-quick) var(--ease-out);
}
/* 手指还按着的时候不能有过渡：那会让跟随慢半拍。 */
.lightbox__figure--moving {
  transition: none;
  cursor: grabbing;
}
.lightbox__figure--zoomed {
  cursor: grab;
}
.lightbox__close {
  position: absolute;
  top: calc(12px + env(safe-area-inset-top, 0px));
  right: calc(12px + env(safe-area-inset-right, 0px));
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: var(--ink);
  background: var(--raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.lightbox__close:hover {
  background: var(--fill-2);
}
/* 触屏上手指点得中：画出来 36px，能点的范围撑到 44×44。 */
@media (pointer: coarse) {
  .lightbox__close::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
</style>
