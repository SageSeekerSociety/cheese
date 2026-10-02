<script setup lang="ts">
import type { Point, RasterRegion, RasterSelection } from './designRegion'
import type { NoteRect, RegionNoteGeometry } from './designRegionNotePosition'
import type { SketchStroke, SketchTool } from './designSketch'
import type { ContentProfile } from './designSnap'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import DesignRasterRegion from './DesignRasterRegion.vue'
import { composeSketch, sampleImage, SKETCH_COLORS, strokeWidth } from './designSketch'
import DesignSketchCanvas from './DesignSketchCanvas.vue'
import DesignSketchOverlay from './DesignSketchOverlay.vue'
import DesignSketchToolbar from './DesignSketchToolbar.vue'
import { blockAt, contentProfile } from './designSnap'

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
const emit = defineEmits<{
  region: [selection: RasterSelection]
  /** 画完的那张合成图；上传和发消息由外面做，这里只管把它做出来。 */
  annotate: [
    payload: {
      blob: Blob
      filename: string
      naturalWidth: number
      naturalHeight: number
      count: number
      note: string
    },
  ]
}>()
const pane = ref<HTMLElement | null>(null)
const viewport = ref<HTMLElement | null>(null)
const selectedBox = ref<HTMLElement | null>(null)
const toolbar = ref<InstanceType<typeof DesignSketchToolbar> | null>(null)
const focusOrigin = ref<Element | null>(null)
const geometry = ref<RegionNoteGeometry | null>(null)
const image = ref<HTMLImageElement | null>(null)
const natural = ref({ width: 0, height: 0 })
const available = ref(0)
const zoom = ref(1)
const fitted = ref(true)
const standaloneRegion = ref<RasterRegion | null>(null)

const tool = ref<SketchTool>('select')
const color = ref<string>(SKETCH_COLORS[0])
const strokes = ref<SketchStroke[]>([])
const undone = ref<SketchStroke[]>([])
/** 图上画得下箭头，说不清「改成什么」，所以那一句是必填的。 */
const note = ref('')
const profile = ref<ContentProfile | null>(null)
const textEditing = ref(false)
const exporting = ref(false)
const spaceHeld = ref(false)
const panning = ref(false)

/** 框选是默认工具：打开就能拖，不用先点一下按钮。 */
const selecting = computed(() => tool.value === 'select')
const drawing = computed(() => tool.value !== 'select')
const penWidth = computed(() => strokeWidth(natural.value.width))
const canUndo = computed(() => strokes.value.length > 0)
const canRedo = computed(() => undone.value.length > 0)
const canSend = computed(() => strokes.value.length > 0 && note.value.trim().length > 0 && !textEditing.value)

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
  const button = toolbar.value?.selectButton
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
    standaloneRegion.value = null
    geometry.value = null
    fitted.value = true
    strokes.value = []
    undone.value = []
    note.value = ''
    profile.value = null
  },
  { flush: 'sync' }
)
watch(
  () => props.selectionEnabled,
  () => {
    standaloneRegion.value = null
    geometry.value = null
  },
  { flush: 'sync' }
)
function loaded(event: Event) {
  const current = image.value
  if (!current || event.currentTarget !== current || current.getAttribute('src') !== props.src) return
  natural.value = { width: current.naturalWidth, height: current.naturalHeight }
  readProfile(current)
}
/** 内容分界线读一次就够：它只随图和版本变，不随缩放变。 */
function readProfile(current: HTMLImageElement) {
  const sample = sampleImage(current)
  profile.value = sample ? contentProfile(sample, current.naturalWidth, current.naturalHeight) : null
}
function setZoom(value: number) {
  zoom.value = Math.max(0.1, Math.min(3, value))
  fitted.value = false
}
/** 滚轮缩放：光标底下那个点不动，缩放才不「跑掉」。 */
let anchor: { clientX: number; clientY: number; naturalX: number; naturalY: number } | null = null
function wheel(event: WheelEvent) {
  const current = image.value
  const bounds = current?.getBoundingClientRect()
  if (!current || !bounds?.width || !natural.value.width) return
  const before = bounds.width / natural.value.width
  anchor = {
    clientX: event.clientX,
    clientY: event.clientY,
    naturalX: (event.clientX - bounds.left) / before,
    naturalY: (event.clientY - bounds.top) / before,
  }
  setZoom(scale.value * Math.exp(-event.deltaY / 400))
  void nextTick(applyAnchor)
}
function applyAnchor() {
  const pending = anchor
  anchor = null
  const host = pane.value
  const current = image.value
  if (!pending || !host || !current || !natural.value.width) return
  const bounds = current.getBoundingClientRect()
  const after = bounds.width / natural.value.width
  if (!after) return
  host.scrollLeft += bounds.left + pending.naturalX * after - pending.clientX
  host.scrollTop += bounds.top + pending.naturalY * after - pending.clientY
}
/** 空格或中键按住拖动＝平移，和画布类应用一套手感。 */
let pan: { x: number; y: number; left: number; top: number } | null = null
function panStart(event: PointerEvent) {
  rememberFocus()
  const host = pane.value
  if (!host) return
  if (!spaceHeld.value && event.button !== 1) return
  event.preventDefault()
  event.stopPropagation()
  panning.value = true
  pan = { x: event.clientX, y: event.clientY, left: host.scrollLeft, top: host.scrollTop }
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}
function panMove(event: PointerEvent) {
  const host = pane.value
  if (!host || !pan) return
  host.scrollLeft = pan.left - (event.clientX - pan.x)
  host.scrollTop = pan.top - (event.clientY - pan.y)
}
function panEnd() {
  pan = null
  panning.value = false
}
function cancelSelection() {
  tool.value = 'select'
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
  if (props.activeRegion === undefined) standaloneRegion.value = selection.region
  emit('region', { ...selection, identity: props.identity })
}
function addStroke(stroke: SketchStroke) {
  strokes.value = [...strokes.value, stroke]
  // 画了新的一笔，原来撤销掉的那些就不该再回来了。
  undone.value = []
}
function undo() {
  const last = strokes.value.at(-1)
  if (!last) return
  strokes.value = strokes.value.slice(0, -1)
  undone.value = [last, ...undone.value]
}
function redo() {
  const [next, ...rest] = undone.value
  if (!next) return
  undone.value = rest
  strokes.value = [...strokes.value, next]
}
function clearStrokes() {
  strokes.value = []
  undone.value = []
}
/** 点一下内容块：不画东西，直接把那一块框出来并编上号。 */
function pickBlock(point: Point) {
  const bounds = profile.value
  if (!bounds || !natural.value.width) return
  addStroke({
    tool: 'rect',
    color: color.value,
    width: penWidth.value,
    region: blockAt(point, bounds, natural.value.width, natural.value.height),
  })
}
async function sendAnnotated() {
  const current = image.value
  if (!current || !strokes.value.length || exporting.value) return
  exporting.value = true
  try {
    const blob = await composeSketch(current, strokes.value)
    if (!blob) return
    const base = props.alt.replace(/\.[^.]+$/, '') || 'image'
    emit('annotate', {
      blob,
      filename: `${base}-annotated.png`,
      naturalWidth: natural.value.width,
      naturalHeight: natural.value.height,
      count: strokes.value.length,
      note: note.value.trim(),
    })
  } finally {
    exporting.value = false
  }
}
function isTyping(target: EventTarget | null) {
  const element = target as HTMLElement | null
  if (!element) return false
  return (
    element.isContentEditable === true ||
    ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName) ||
    element.closest?.('input, textarea, [contenteditable="true"]') != null
  )
}
function keyDown(event: KeyboardEvent) {
  if (event.code !== 'Space' || isTyping(event.target)) return
  // 空格在浏览器里是翻页，按住时要把它让给平移。
  event.preventDefault()
  spaceHeld.value = true
}
function keyUp(event: KeyboardEvent) {
  if (event.code === 'Space') spaceHeld.value = false
}
function blur() {
  spaceHeld.value = false
  panEnd()
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
  window.addEventListener('keydown', keyDown)
  window.addEventListener('keyup', keyUp)
  window.addEventListener('blur', blur)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  document.removeEventListener('scroll', scheduleMeasure, true)
  window.removeEventListener('resize', scheduleMeasure)
  window.visualViewport?.removeEventListener('resize', scheduleMeasure)
  window.visualViewport?.removeEventListener('scroll', scheduleMeasure)
  window.removeEventListener('keydown', keyDown)
  window.removeEventListener('keyup', keyUp)
  window.removeEventListener('blur', blur)
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
      <slot name="actions" />
    </div>
    <DesignSketchToolbar
      ref="toolbar"
      v-model:note="note"
      :tool="tool"
      :color="color"
      :can-undo="canUndo"
      :can-redo="canRedo"
      :has-strokes="strokes.length > 0"
      :can-send="canSend"
      :can-select="selectionEnabled && !!natural.width"
      :busy="exporting"
      @pick="tool = $event"
      @recolor="color = $event"
      @undo="undo"
      @redo="redo"
      @clear="clearStrokes"
      @send="sendAnnotated"
    />
    <output v-if="selectedRegion" class="t-meta" aria-live="polite">{{
      t('design.selectedRegion', selectedRegion)
    }}</output>
    <div
      ref="viewport"
      class="design-image__viewport"
      :class="{ 'is-pannable': spaceHeld, 'is-panning': panning }"
      @pointerdown.capture="panStart"
      @pointermove.capture="panMove"
      @pointerup.capture="panEnd"
      @pointercancel.capture="panEnd"
    >
      <div ref="pane" class="design-image__pane" @wheel.prevent="wheel">
        <div class="design-image__sheet" :style="dimensions">
          <img :key="`${identity}:${src}`" ref="image" :src="src" :alt="alt" draggable="false" @load="loaded" />
          <DesignSketchOverlay v-if="strokes.length" :strokes="strokes" :scale="scale" :natural-width="natural.width" />
          <div
            v-if="selectedRegion"
            ref="selectedBox"
            class="design-image__selection"
            :style="selectedStyle"
            aria-hidden="true"
          />
          <DesignRasterRegion
            :image="image"
            :enabled="selecting && selectionEnabled"
            :identity="regionIdentity"
            :profile="profile"
            @select="selected"
            @cancel="cancelSelection"
          />
          <DesignSketchCanvas
            v-if="drawing"
            :image="image"
            :identity="regionIdentity"
            :tool="tool"
            :color="color"
            :width="penWidth"
            :profile="profile"
            @stroke="addStroke"
            @pick-block="pickBlock"
            @text-editing="textEditing = $event"
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
.design-image__viewport.is-pannable {
  cursor: grab;
}
.design-image__viewport.is-panning {
  cursor: grabbing;
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
