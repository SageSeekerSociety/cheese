<script setup lang="ts">
import type { ImageGeometry, Point, RasterRegion, RasterSelection } from './designRegion'
import type { ContentProfile } from './designSnap'

import { computed, ref, watch } from 'vue'

import { displayedRegion, imagePoint, imageRegion } from './designRegion'
import { blockAt, snapRegion } from './designSnap'

import { t } from '@/i18n'

const props = defineProps<{
  image: HTMLImageElement | null
  enabled: boolean
  identity: string
  /** 有内容分界线时就智能起来：拖动吸附到块的边，单击直接框住整块。 */
  profile?: ContentProfile | null
}>()
const emit = defineEmits<{ select: [selection: RasterSelection]; cancel: [] }>()
const start = ref<Point | null>(null)
const draft = ref<RasterRegion | null>(null)
const geometry = ref<ImageGeometry | null>(null)
/** 鼠标底下那一块：还没按下去，先让人看见「点下去会框住这里」。 */
const hover = ref<RasterRegion | null>(null)
const hoverGeometry = ref<ImageGeometry | null>(null)
let pointer: number | null = null
let captured: Pick<RasterSelection, 'identity' | 'src'> | null = null
const rectangle = computed(() => (draft.value && geometry.value ? displayedRegion(draft.value, geometry.value) : null))
const hoverRectangle = computed(() =>
  hover.value && hoverGeometry.value ? displayedRegion(hover.value, hoverGeometry.value) : null
)
/** 底下真的认出了一块（不是退回整张图）时，光标换成十字——看得见才敢点。 */
const targeting = computed(() => {
  const region = hover.value
  const size = hoverGeometry.value
  if (!region || !size) return false
  return region.width < size.naturalWidth || region.height < size.naturalHeight
})
/** 图当前在屏幕上的位置与尺寸；每次问都现算，滚动和缩放之后才不会指错地方。 */
function measure(): ImageGeometry | null {
  const image = props.image
  if (!image?.complete) return null
  const rect = image.getBoundingClientRect()
  if (!image.naturalWidth || !image.naturalHeight || !rect.width || !rect.height) return null
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  }
}
function reset() {
  start.value = null
  draft.value = null
  geometry.value = null
  hover.value = null
  hoverGeometry.value = null
  pointer = null
  captured = null
}
function down(event: PointerEvent) {
  if (!props.enabled || !props.image?.complete || event.button !== 0) return
  const size = measure()
  if (!size) return
  geometry.value = size
  // 已经在拖了，候选框就该让位给真正在画的那个框。
  hover.value = null
  hoverGeometry.value = null
  start.value = { x: event.clientX, y: event.clientY }
  captured = { identity: props.identity, src: props.image!.getAttribute('src') ?? '' }
  pointer = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  event.preventDefault()
}
/**
 * 鼠标划过时把光标下那一块框出来——「智能识别」得在按下去之前就看得见，
 * 否则每换一处都要先点一下、不满意再点别处，等于在试。
 */
function hoverAt(event: PointerEvent) {
  const profile = props.profile
  if (!props.enabled || !profile) return
  const size = measure()
  hoverGeometry.value = size
  const point = size ? imagePoint({ x: event.clientX, y: event.clientY }, size) : null
  hover.value = point ? blockAt(point, profile, size!.naturalWidth, size!.naturalHeight) : null
}
function move(event: PointerEvent) {
  if (event.pointerId !== pointer || !start.value || !geometry.value) {
    hoverAt(event)
    return
  }
  draft.value = imageRegion(start.value, { x: event.clientX, y: event.clientY }, geometry.value)
}
function leave() {
  if (pointer === null) {
    hover.value = null
    hoverGeometry.value = null
  }
}
/** 一次点击（而不是拖动）：位移不到 4 个显示像素，人没打算框，只是想点那一块。 */
function tapped(region: RasterRegion | null, size: ImageGeometry) {
  if (!region) return true
  return (region.width * size.width) / size.naturalWidth < 4 && (region.height * size.height) / size.naturalHeight < 4
}
function up(event: PointerEvent) {
  if (event.pointerId !== pointer) return
  move(event)
  const region = draft.value
  const context = captured
  const size = geometry.value
  const current = props.image
  const valid =
    props.enabled &&
    current &&
    context &&
    size &&
    context.identity === props.identity &&
    context.src === (current.getAttribute('src') ?? '') &&
    size.naturalWidth === current.naturalWidth &&
    size.naturalHeight === current.naturalHeight
  const profile = props.profile
  let picked = region
  if (profile && size && start.value) {
    if (tapped(region, size)) {
      const point = imagePoint(start.value, size)
      picked = point ? blockAt(point, profile, size.naturalWidth, size.naturalHeight) : null
    } else if (region) picked = snapRegion(region, profile)
  }
  reset()
  if (picked && valid)
    emit('select', { region: picked, ...context, naturalWidth: size.naturalWidth, naturalHeight: size.naturalHeight })
}
function cancel() {
  reset()
  emit('cancel')
}
watch([() => props.enabled, () => props.identity, () => props.image], reset, { flush: 'sync' })
</script>

<template>
  <div
    v-if="enabled"
    class="raster-region"
    :class="{ 'is-targeting': targeting }"
    tabindex="0"
    role="group"
    :aria-label="t('design.region')"
    @pointerdown="down"
    @pointermove="move"
    @pointerup="up"
    @pointercancel="reset"
    @lostpointercapture="reset"
    @pointerleave="leave"
    @keydown.esc.prevent="cancel"
  >
    <div
      v-if="hoverRectangle"
      class="raster-region__hover"
      :style="{
        left: `${hoverRectangle.x}px`,
        top: `${hoverRectangle.y}px`,
        width: `${hoverRectangle.width}px`,
        height: `${hoverRectangle.height}px`,
      }"
    />
    <div
      v-if="rectangle"
      class="raster-region__box"
      :style="{
        left: `${rectangle.x}px`,
        top: `${rectangle.y}px`,
        width: `${rectangle.width}px`,
        height: `${rectangle.height}px`,
      }"
    />
  </div>
</template>

<style scoped>
.raster-region {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  touch-action: none;
}
/*
 * 底下的十字黑线压在白线上面：整条线横竖贯到光标框外，落在深色内容和浅色
 * 空白上都看得见。热点在正中间，指哪儿框哪儿。
 *
 * SVG 走 base64 而不是原样塞 UTF-8：里面的 `<`、`"`、空格在 url() 里要靠
 * 引号兜着，一旦经过打包器或别处的转义规则就容易被吃掉，编成 base64 之后
 * 只剩 [A-Za-z0-9+/=]，到哪都一样。
 */
.raster-region.is-targeting {
  cursor:
    url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCI+PGcgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIzIj48cGF0aCBkPSJNMTIgMHYyNE0wIDEyaDI0Ii8+PC9nPjxnIGZpbGw9Im5vbmUiIHN0cm9rZT0iYmxhY2siIHN0cm9rZS13aWR0aD0iMSI+PHBhdGggZD0iTTEyIDB2MjRNMCAxMmgyNCIvPjwvZz48L3N2Zz4=')
      12 12,
    crosshair;
}
.raster-region:focus-visible {
  outline: 2px solid var(--accent);
}
.raster-region__box {
  position: absolute;
  pointer-events: none;
  border: 2px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
/* 候选态：虚线、浅一档，和已经框住的那一块（实线加淡底）一眼分得开。 */
.raster-region__hover {
  position: absolute;
  pointer-events: none;
  border: 1px dashed var(--accent);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
</style>
