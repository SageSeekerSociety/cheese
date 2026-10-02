<script setup lang="ts">
import type { RasterRegion, RasterSelection } from './designRegion'
import type { NoteRect, RegionNoteGeometry } from './designRegionNotePosition'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import DesignRasterRegion from './DesignRasterRegion.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    src: string
    alt: string
    identity: string
    selectionEnabled?: boolean
    /** Undefined keeps standalone selection; null is an explicitly cleared controlled region. */
    activeRegion?: RasterRegion | null
  }>(),
  {
    selectionEnabled: true,
    activeRegion: undefined,
  }
)
const emit = defineEmits<{ region: [selection: RasterSelection] }>()
const pane = ref<HTMLElement | null>(null)
const viewport = ref<HTMLElement | null>(null)
const selectedBox = ref<HTMLElement | null>(null)
const regionButton = ref<HTMLButtonElement | null>(null)
const focusOrigin = ref<Element | null>(null)
const geometry = ref<RegionNoteGeometry | null>(null)
const image = ref<HTMLImageElement | null>(null)
const natural = ref({ width: 0, height: 0 })
const available = ref(0)
const zoom = ref(1)
const fitted = ref(true)
const selecting = ref(false)
const standaloneRegion = ref<RasterRegion | null>(null)
const selectedRegion = computed(() => (props.activeRegion === undefined ? standaloneRegion.value : props.activeRegion))
const regionIdentity = computed(() => `${props.identity}:${scale.value}:${available.value}`)
const scale = computed(() => {
  if (!fitted.value) return zoom.value
  const width = natural.value.width
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(available.value)) return 0
  return Math.min(1, Math.max(0, (available.value - 32) / width))
})
const dimensions = computed(() =>
  natural.value.width
    ? {
        width: `${natural.value.width * scale.value}px`,
        height: `${natural.value.height * scale.value}px`,
      }
    : {}
)
let observer: ResizeObserver | null = null
let frame = 0
function clippingAncestors(element: HTMLElement) {
  const clips: { element: HTMLElement; horizontal: boolean; vertical: boolean }[] = []
  for (let parent = element.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent)
    const horizontal = /^(auto|scroll|hidden|clip)$/.test(style.overflowX)
    const vertical = /^(auto|scroll|hidden|clip)$/.test(style.overflowY)
    if (horizontal || vertical) clips.push({ element: parent, horizontal, vertical })
  }
  return clips
}
function measure() {
  frame = 0
  const host = viewport.value
  const element = pane.value
  const selected = selectedBox.value
  if (!host || !element || !selected || !selectedRegion.value) {
    geometry.value = null
    return
  }
  const origin = host.getBoundingClientRect()
  const bounds = element.getBoundingClientRect()
  const visual = window.visualViewport
  let left = Math.max(bounds.left + element.clientLeft, visual?.offsetLeft ?? 0)
  let top = Math.max(bounds.top + element.clientTop, visual?.offsetTop ?? 0)
  let right = Math.min(
    bounds.left + element.clientLeft + element.clientWidth,
    (visual?.offsetLeft ?? 0) + (visual?.width ?? window.innerWidth)
  )
  let bottom = Math.min(
    bounds.top + element.clientTop + element.clientHeight,
    (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight)
  )
  for (const clip of clippingAncestors(element)) {
    const box = clip.element.getBoundingClientRect()
    if (clip.horizontal) {
      left = Math.max(left, box.left + clip.element.clientLeft)
      right = Math.min(right, box.left + clip.element.clientLeft + clip.element.clientWidth)
    }
    if (clip.vertical) {
      top = Math.max(top, box.top + clip.element.clientTop)
      bottom = Math.min(bottom, box.top + clip.element.clientTop + clip.element.clientHeight)
    }
  }
  const region = selected.getBoundingClientRect()
  const regionLeft = Math.max(region.left, left)
  const regionTop = Math.max(region.top, top)
  const regionRight = Math.min(region.right, right)
  const regionBottom = Math.min(region.bottom, bottom)
  const local = (x: number, y: number, width: number, height: number): NoteRect => ({
    left: x - origin.left,
    top: y - origin.top,
    width,
    height,
  })
  geometry.value = {
    viewport: local(left, top, Math.max(0, right - left), Math.max(0, bottom - top)),
    region:
      regionRight > regionLeft && regionBottom > regionTop
        ? local(regionLeft, regionTop, regionRight - regionLeft, regionBottom - regionTop)
        : null,
  }
}
function scheduleMeasure() {
  if (!frame) frame = requestAnimationFrame(measure)
}
function restoreFocus(resourceKey: string) {
  const button = regionButton.value
  if (!button?.isConnected || button.disabled || resourceKey !== props.identity) return
  const document = button.ownerDocument
  if (!document.hasFocus() || (document.activeElement && document.activeElement !== document.body)) return
  button.focus({ preventScroll: true })
}
function rememberFocus() {
  if (selecting.value) focusOrigin.value = pane.value?.ownerDocument.activeElement ?? null
}
const selectedStyle = computed(() => {
  const region = selectedRegion.value
  return region
    ? {
        left: `${region.x * scale.value}px`,
        top: `${region.y * scale.value}px`,
        width: `${region.width * scale.value}px`,
        height: `${region.height * scale.value}px`,
      }
    : {}
})
watch(
  pane,
  (element) => {
    observer?.disconnect()
    if (!element) return
    observer = new ResizeObserver(() => {
      available.value = element.clientWidth
      scheduleMeasure()
    })
    observer.observe(element)
    for (const clip of clippingAncestors(element)) observer.observe(clip.element)
  },
  { flush: 'post' }
)
watch(
  [() => props.src, () => props.identity],
  () => {
    natural.value = { width: 0, height: 0 }
    selecting.value = false
    standaloneRegion.value = null
    geometry.value = null
    fitted.value = true
  },
  { flush: 'sync' }
)
watch(
  () => props.selectionEnabled,
  () => {
    selecting.value = false
    standaloneRegion.value = null
    geometry.value = null
  },
  { flush: 'sync' }
)
function loaded(event: Event) {
  const current = image.value
  if (current && event.currentTarget === current && current.getAttribute('src') === props.src)
    natural.value = { width: current.naturalWidth, height: current.naturalHeight }
}
function setZoom(value: number) {
  zoom.value = Math.max(0.1, Math.min(3, value))
  fitted.value = false
}
function selected(selection: RasterSelection) {
  const current = image.value
  if (
    !props.selectionEnabled ||
    !current ||
    selection.identity !== regionIdentity.value ||
    selection.src !== props.src ||
    current.getAttribute('src') !== selection.src ||
    selection.naturalWidth !== current.naturalWidth ||
    selection.naturalHeight !== current.naturalHeight
  )
    return
  selecting.value = false
  if (props.activeRegion === undefined) standaloneRegion.value = selection.region
  emit('region', { ...selection, identity: props.identity })
}
watch(
  () => props.activeRegion,
  () => {
    standaloneRegion.value = null
  },
  { flush: 'sync' }
)
watch([scale, available, selectedRegion, selectedBox], scheduleMeasure, { flush: 'post' })
onMounted(() => {
  document.addEventListener('scroll', scheduleMeasure, true)
  window.addEventListener('resize', scheduleMeasure)
  window.visualViewport?.addEventListener('resize', scheduleMeasure)
  window.visualViewport?.addEventListener('scroll', scheduleMeasure)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  document.removeEventListener('scroll', scheduleMeasure, true)
  window.removeEventListener('resize', scheduleMeasure)
  window.visualViewport?.removeEventListener('resize', scheduleMeasure)
  window.visualViewport?.removeEventListener('scroll', scheduleMeasure)
})
</script>

<template>
  <section class="design-image">
    <div class="design-image__tools" role="group" :aria-label="t('design.viewportTools')">
      <button type="button" :aria-label="t('design.zoomOut')" :disabled="scale <= 0.1" @click="setZoom(scale / 1.25)">
        −
      </button>
      <output class="t-meta" :aria-label="t('design.scale')">{{ Math.round(scale * 100) }}%</output>
      <button type="button" :aria-label="t('design.zoomIn')" :disabled="scale >= 3" @click="setZoom(scale * 1.25)">
        +
      </button>
      <button type="button" :aria-pressed="fitted" @click="fitted = true">{{ t('design.fit') }}</button>
      <button
        ref="regionButton"
        type="button"
        :disabled="!natural.width || !selectionEnabled"
        :title="selectionEnabled ? undefined : t('design.regionUnavailable')"
        :aria-pressed="selecting"
        @click="selecting = !selecting"
      >
        {{ t(selecting ? 'design.cancelRegion' : 'design.region') }}
      </button>
      <slot name="actions" />
    </div>
    <output v-if="selectedRegion" class="t-meta" aria-live="polite">{{
      t('design.selectedRegion', selectedRegion)
    }}</output>
    <div ref="viewport" class="design-image__viewport" @pointerdown.capture="rememberFocus">
      <div ref="pane" class="design-image__pane" @scroll="selecting = false">
        <div class="design-image__sheet" :style="dimensions">
          <img :key="`${identity}:${src}`" ref="image" :src="src" :alt="alt" draggable="false" @load="loaded" />
          <div
            v-if="selectedRegion && !selecting"
            ref="selectedBox"
            class="design-image__selection"
            :style="selectedStyle"
            aria-hidden="true"
          />
          <DesignRasterRegion
            :image="image"
            :enabled="selecting && selectionEnabled"
            :identity="regionIdentity"
            @select="selected"
            @cancel="selecting = false"
          />
        </div>
      </div>
      <div class="design-image__overlay">
        <slot name="region-note" :geometry="geometry" :restore-focus="restoreFocus" :focus-origin="focusOrigin" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.design-image {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.design-image__tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--line);
}
.design-image__tools button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.design-image__tools button:hover:not(:disabled) {
  background: var(--fill-2);
}
.design-image__tools button:disabled {
  color: var(--faint);
}
.design-image__tools button:focus-visible {
  outline: 2px solid var(--accent);
}
.design-image__viewport {
  position: relative;
  flex: 1;
  min-height: 0;
}
.design-image__pane {
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  overflow: auto;
  padding: 16px;
}
.design-image__overlay {
  position: absolute;
  inset: 0;
  overflow: clip;
  pointer-events: none;
}
.design-image__sheet {
  position: relative;
}
.design-image__sheet img {
  display: block;
  width: 100%;
  height: 100%;
}
.design-image__selection {
  position: absolute;
  pointer-events: none;
  box-sizing: border-box;
  border: 2px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
