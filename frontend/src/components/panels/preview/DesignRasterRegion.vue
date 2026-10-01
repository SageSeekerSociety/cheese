<script setup lang="ts">
import type { ImageGeometry, Point, RasterRegion } from './designRegion'

import { computed, ref, watch } from 'vue'

import { displayedRegion, imageRegion } from './designRegion'

import { t } from '@/i18n'

const props = defineProps<{ image: HTMLImageElement | null; enabled: boolean; identity: string }>()
const emit = defineEmits<{ select: [region: RasterRegion]; cancel: [] }>()
const start = ref<Point | null>(null)
const draft = ref<RasterRegion | null>(null)
const geometry = ref<ImageGeometry | null>(null)
let pointer: number | null = null
const rectangle = computed(() => (draft.value && geometry.value ? displayedRegion(draft.value, geometry.value) : null))
function reset() {
  start.value = null
  draft.value = null
  geometry.value = null
  pointer = null
}
function down(event: PointerEvent) {
  if (!props.enabled || !props.image?.complete || event.button !== 0) return
  const image = props.image
  const rect = image.getBoundingClientRect()
  if (!image.naturalWidth || !rect.width || !rect.height) return
  geometry.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  }
  start.value = { x: event.clientX, y: event.clientY }
  pointer = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  event.preventDefault()
}
function move(event: PointerEvent) {
  if (event.pointerId !== pointer || !start.value || !geometry.value) return
  draft.value = imageRegion(start.value, { x: event.clientX, y: event.clientY }, geometry.value)
}
function up(event: PointerEvent) {
  if (event.pointerId !== pointer) return
  move(event)
  const region = draft.value
  reset()
  if (region) emit('select', region)
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
    tabindex="0"
    role="group"
    :aria-label="t('design.region')"
    @pointerdown="down"
    @pointermove="move"
    @pointerup="up"
    @pointercancel="reset"
    @lostpointercapture="reset"
    @keydown.esc.prevent="cancel"
  >
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
.raster-region:focus-visible {
  outline: 2px solid var(--accent);
}
.raster-region__box {
  position: absolute;
  pointer-events: none;
  border: 2px solid var(--accent);
  background: var(--accent-weak);
}
</style>
