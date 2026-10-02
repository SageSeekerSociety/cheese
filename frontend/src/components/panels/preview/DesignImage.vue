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
    /**
     * 这一格正显示着没有。
     *
     * 撤销、退出、空格平移都挂在 window 上（图未必拿着焦点），而工作面板把收起的那
     * 几页用 v-show 留着——不给这一样，收起来的那张图照样会吃掉空格、会被 ⌘Z 改。
     */
    active?: boolean
    /** Undefined keeps standalone selection; null is an explicitly cleared controlled region. */
    activeRegion?: RasterRegion | null
    /**
     * 滚轮是不是用来缩放的。
     *
     * 面板里这张图嵌在一段要滚的正文里，滚轮得先把那段滚下去；只有图铺满整个
     * 屏幕（全屏）时，把滚轮让给缩放才不抢东西。
     */
    zoomOnWheel?: boolean
  }>(),
  {
    selectionEnabled: true,
    active: true,
    activeRegion: undefined,
    zoomOnWheel: false,
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
/** 画布只在用着会画的工具时挂着，所以这一格多半是空的——Esc 要作废手里的这一笔时用它。 */
const canvas = ref<InstanceType<typeof DesignSketchCanvas> | null>(null)
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
/** 合成失败时的那一句：它发生在这里（画布在这一层），上传失败那句在外面。 */
const sendError = ref('')
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
  // 不缩放时什么都不做，让滚轮去干它本来那件事：滚这段。
  if (!props.zoomOnWheel) return
  event.preventDefault()
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
  sendError.value = ''
  try {
    const blob = await composeSketch(current, strokes.value)
    // 合成不出来（画布不可用，或者压到最小还是太大）时说一声：不说的话，按了按钮
    // 看着像什么都没发生。
    if (!blob) {
      sendError.value = t('design.composeFailed')
      return
    }
    const base = props.alt.replace(/\.[^.]+$/, '') || 'image'
    emit('annotate', {
      blob,
      filename: `${base}-annotated.png`,
      naturalWidth: natural.value.width,
      naturalHeight: natural.value.height,
      count: strokes.value.length,
      note: note.value.trim(),
    })
  } catch (error) {
    sendError.value = error instanceof Error ? error.message : t('design.composeFailed')
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
/**
 * Esc 退出正在进行的操作，做掉了就报 true。
 *
 * 正在图上打字：这次文字作废，画笔留着，方便重打。否则是画到一半（或只是挑着一支
 * 画笔）：手里的这一笔作废，光标交还默认的框选。已经框好的那一块不在这里动——它归
 * 外面那条说明卡管（DesignRegionNote 自己接 Esc）。
 */
function exitCurrent(): boolean {
  if (textEditing.value) {
    canvas.value?.cancel()
    return true
  }
  if (tool.value === 'select') return false
  canvas.value?.cancel()
  cancelSelection()
  return true
}
/**
 * 标注的键盘快捷键：撤销/重做/退出。
 *
 * 焦点落在输入框里时一律让开——那个「说一句要改什么」的框、图上的文字框，都要能用
 * 自己的编辑键（含它们各自的撤销）。没有可撤销/可退的东西时也不拦，键照旧交回浏览器。
 */
function keyDown(event: KeyboardEvent) {
  if (isTyping(event.target)) return
  // 收起来的那几页（工作面板用 v-show 留着的）照样挂着这个 listener，但它们没在看
  // 图：空格该去翻页，⌘Z 也不该去动一张没人看着的图。
  if (!props.active) return
  const mod = event.metaKey || event.ctrlKey
  // ⌘/Ctrl+Z 撤销；⇧⌘/Ctrl+Z 与 Ctrl+Y 重做。认 key 不认 code：撤销绑的是 Z 这个字母。
  if (mod && event.key.toLowerCase() === 'z') {
    if (event.shiftKey ? canRedo.value : canUndo.value) {
      event.preventDefault()
      if (event.shiftKey) redo()
      else undo()
    }
    return
  }
  if (mod && event.key.toLowerCase() === 'y') {
    if (canRedo.value) {
      event.preventDefault()
      redo()
    }
    return
  }
  if (event.key === 'Escape') {
    // 没做到事就不拦：Esc 在浏览器里还管着退出全屏这类事。
    if (exitCurrent()) event.preventDefault()
    return
  }
  if (event.code !== 'Space') return
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
// 收起来的那一刻把手里的东西放下：按着的空格、（真在拖的话）正在进行的平移。
watch(
  () => props.active,
  (on) => {
    if (!on) blur()
  }
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
    <output v-if="sendError" class="design-image__error" role="alert">{{ sendError }}</output>
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
      <div ref="pane" class="design-image__pane" @wheel="wheel">
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
            ref="canvas"
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
.design-image__error {
  padding: 6px 8px;
  color: var(--danger, #e5484d);
  font-size: 13px;
  line-height: var(--lh-13);
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
