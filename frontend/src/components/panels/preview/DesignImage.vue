<script setup lang="ts">
import type { Point, RasterRegion, RasterSelection } from './designRegion'
import type { NoteRect, RegionNoteGeometry } from './designRegionNotePosition'
import type { SketchStroke, SketchTool } from './designSketch'
import type { ContentProfile } from './designSnap'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'

import { clearAnnotationGuard, setAnnotationGuard } from './annotationDiscard'
import DesignRasterRegion from './DesignRasterRegion.vue'
import { composeSketch, fontSize, sampleImage, SKETCH_COLORS, strokeWidth } from './designSketch'
import DesignSketchCanvas from './DesignSketchCanvas.vue'
import { hitTest } from './designSketchHit'
import DesignSketchOverlay from './DesignSketchOverlay.vue'
import {
  applyResize,
  canResize,
  HANDLE_HIT_RADIUS,
  type HandleRole,
  isEmptyStroke,
  isSelectableStroke,
  moveStroke,
  nearestHandle,
  strokeHandles,
} from './designSketchSelection'
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
/**
 * 撤销栈：一整份快照一条。
 *
 * 拖动/缩放只在**松手且真的动过**时才在松手那一刻记一条，移动途中不记；加一笔、删除、
 * 改色、文字编辑提交、清空各算一条。上限 100。
 */
const HISTORY_LIMIT = 100
const undoStack = ref<SketchStroke[][]>([])
const redoStack = ref<SketchStroke[][]>([])
/** 选中的是哪一条（按索引）；除自由笔外都能选中、编辑。 */
const selectedStroke = ref<number | null>(null)
/** 正在编辑的那条已有文字：原文字不画，由输入框呈现。 */
const editingText = ref<{ index: number; value: string } | null>(null)
/** 正在拖/缩一笔（挂在 sheet 上的手势）：撤销/重做这时要拒绝。 */
const interacting = ref(false)
/**
 * 有没有「画了但还没发出去」的笔画。
 *
 * 发出去之后笔画还留在屏上（人看着自己刚标的东西），但那些不再算「会丢的东西」——
 * 所以不是「有笔画就拦」，而是「有笔画、且屏上跟发出去的那份不一样」才拦（见
 * `annotationDiscard`）。
 *
 * 判据是「上一次发出去时的那几笔」，不是一个开关：发过之后撤销再重做，屏上又跟发
 * 出去时一样了，不该再被当成欠着谁。撤销/重做拿回来的是同一批笔画对象（快照只换外
 * 层数组，笔画对象本身是引用），所以逐条按引用比就够，不必深比较。
 */
const sentStrokes = ref<SketchStroke[] | null>(null)
function matchesSent(current: SketchStroke[]): boolean {
  const sent = sentStrokes.value
  return sent !== null && current.length === sent.length && current.every((stroke, index) => stroke === sent[index])
}
const unsent = computed(() => strokes.value.length > 0 && !matchesSent(strokes.value))
/** 走之前那一下确认。对话框画在这一层，登记的守卫是它。 */
const discardPrompt = ref(false)
let resolveDiscard: ((go: boolean) => void) | null = null
/**
 * 还开着的那一次确认。
 *
 * 弹框开着的时候可能又来一次导航（双击页签、先点页签再点关闭、快捷键与点击几乎同时
 * 触发两次 `setTab`/`closeFile`）。没有它的话 `confirmDiscard` 会把上一次的 resolver
 * 顶掉，第一次那一下永远等不到答复，它该做的切换/关闭就静默丢了。存着这一份，第二次
 * 直接拿同一个 Promise：一次回答答住两次。
 */
let discardPending: Promise<boolean> | null = null
const discardKeep = ref<HTMLButtonElement | null>(null)
/** 弹框那张卡：焦点陷阱围着它转，别把字落到后面没被盖住的界面上。 */
const discardCard = ref<HTMLElement | null>(null)
/** 弹框冒出来之前焦点在哪：关了以后还回去，免得键盘用的人被打回页首。 */
const discardOrigin = ref<HTMLElement | null>(null)
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
const canUndo = computed(() => undoStack.value.length > 0)
const canRedo = computed(() => redoStack.value.length > 0)
const canSend = computed(() => strokes.value.length > 0 && note.value.trim().length > 0 && !textEditing.value)
/** 选中的那一笔。 */
const selectedShape = computed(() => {
  const index = selectedStroke.value
  if (index === null) return null
  const stroke = strokes.value[index]
  return stroke && isSelectableStroke(stroke) ? stroke : null
})
/** 真会丢掉东西的时候（画了、且还没发），才值得拦一道。 */
const wouldLoseStrokes = computed(() => strokes.value.length > 0 && unsent.value)

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
    undoStack.value = []
    redoStack.value = []
    selectedStroke.value = null
    editingText.value = null
    sentStrokes.value = null
    note.value = ''
    profile.value = null
  },
  { flush: 'sync' }
)
// 换到会画的工具就放下选中的那一条：那块把手不该再盖在画布上抢指针。
// 切回 select（含「画完自动选中」那一下）时留着选中——那是刚画完的那一笔。
watch(tool, (next) => {
  if (next !== 'select') {
    if (editingText.value) commitTextEdit()
    selectedStroke.value = null
  }
})
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
/** 记一条历史快照（整份笔画数组），并把重做栈清掉。 */
function pushHistory(before: SketchStroke[]) {
  undoStack.value = [...undoStack.value, before].slice(-HISTORY_LIMIT)
  redoStack.value = []
}
function addStroke(stroke: SketchStroke) {
  const before = strokes.value
  strokes.value = [...before, stroke]
  pushHistory(before)
  // 画完一笔非自由笔的图形就选中它、把工具交回 select；自由笔连着画，不打断。
  if (stroke.tool !== 'pen') {
    selectedStroke.value = strokes.value.length - 1
    tool.value = 'select'
  }
}
function undo() {
  const before = undoStack.value.at(-1)
  if (!before) return
  redoStack.value = [...redoStack.value, strokes.value]
  undoStack.value = undoStack.value.slice(0, -1)
  strokes.value = before
  selectedStroke.value = null
}
function redo() {
  const next = redoStack.value.at(-1)
  if (!next) return
  undoStack.value = [...undoStack.value, strokes.value]
  redoStack.value = redoStack.value.slice(0, -1)
  strokes.value = next
  selectedStroke.value = null
}
function clearStrokes() {
  if (!strokes.value.length) return
  pushHistory(strokes.value)
  strokes.value = []
  selectedStroke.value = null
}
/** 丢弃那次「放弃这些标注？」：连历史一起清掉，不给撤回来的机会。 */
function dropAnnotations() {
  strokes.value = []
  undoStack.value = []
  redoStack.value = []
  selectedStroke.value = null
  editingText.value = null
  textEditing.value = false
}
/** 改选中对象的颜色；没选中就只改「下一笔的颜色」。 */
function recolor(swatch: string) {
  const index = selectedStroke.value
  const stroke = index === null ? null : strokes.value[index]
  if (stroke && isSelectableStroke(stroke)) {
    const before = strokes.value
    strokes.value = before.map((current, at) => (at === index ? { ...current, color: swatch } : current))
    pushHistory(before)
  }
  color.value = swatch
}
/** Backspace / Delete 删掉选中的那一笔。 */
function deleteSelected() {
  const index = selectedStroke.value
  if (index === null || !strokes.value[index]) return
  const before = strokes.value
  strokes.value = before.filter((_, at) => at !== index)
  pushHistory(before)
  selectedStroke.value = null
}

/** 图片当前在屏幕上的几何：命中测试与手势都拿它把显示像素换算成原图像素。 */
function imageGeometry() {
  const element = image.value
  if (!element || !natural.value.width || !natural.value.height) return null
  const rect = element.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  return {
    left: rect.left,
    top: rect.top,
    scale: rect.width / natural.value.width,
  }
}

/** 一次按下开始的手势：拖动整笔，或拖某个把手缩放。松手时若动过才记一条历史。 */
type Gesture = {
  kind: 'move' | 'resize'
  role: HandleRole | null
  index: number
  origin: Exclude<SketchStroke, { tool: 'pen' }>
  before: SketchStroke[]
  /** 原图像素：按下时指针落在哪儿，用来算位移。 */
  start: Point
  /** 屏幕像素：按下时指针落在哪儿，用来算 3px / 10px 门槛。 */
  client: Point
  touch: boolean
  started: boolean
}
let gesture: Gesture | null = null
let gesturePointer: number | null = null

function beginGesture(
  index: number,
  role: HandleRole | null,
  kind: 'move' | 'resize',
  event: PointerEvent,
  geometry: { left: number; top: number; scale: number }
) {
  const stroke = strokes.value[index]
  if (!stroke || !isSelectableStroke(stroke)) return
  gesture = {
    kind,
    role,
    index,
    origin: stroke,
    before: strokes.value,
    start: { x: (event.clientX - geometry.left) / geometry.scale, y: (event.clientY - geometry.top) / geometry.scale },
    client: { x: event.clientX, y: event.clientY },
    touch: event.pointerType === 'touch',
    started: false,
  }
  gesturePointer = event.pointerId
  interacting.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  event.preventDefault()
  event.stopPropagation()
}

/**
 * 选中工具下按下：先看把手，再看对象，都没中就放下选中、把指针让给区域选择器。
 *
 * 用 capture 拦截：区域选择器是 sheet 的子层，不先拦一道，它会先吃掉这一下。
 */
function sheetDown(event: PointerEvent) {
  if (event.button !== 0 || tool.value !== 'select') return
  const geometry = imageGeometry()
  if (!geometry) return
  const display = { x: event.clientX - geometry.left, y: event.clientY - geometry.top }
  const touch = event.pointerType === 'touch'
  const current = selectedShape.value
  const currentIndex = selectedStroke.value
  if (current && currentIndex !== null) {
    const role = nearestHandle(strokeHandles(current, geometry.scale, natural.value.width), display, HANDLE_HIT_RADIUS)
    if (role) {
      // 文字没有可缩的框：拖它的把手等于平移。
      beginGesture(currentIndex, role, canResize(current) ? 'resize' : 'move', event, geometry)
      return
    }
  }
  const index = hitTest(strokes.value, display, { touch, scale: geometry.scale, naturalWidth: natural.value.width })
  if (index === null) {
    // 点在空白处：放下选中，让指针落到下面的区域选择器上（交给芝士）。
    selectedStroke.value = null
    return
  }
  selectedStroke.value = index
  beginGesture(index, null, 'move', event, geometry)
}

function sheetMove(event: PointerEvent) {
  const active = gesture
  if (!active || event.pointerId !== gesturePointer) return
  const geometry = imageGeometry()
  if (!geometry) return
  if (!active.started) {
    const moved = Math.hypot(event.clientX - active.client.x, event.clientY - active.client.y)
    if (moved < (active.touch ? 10 : 3)) return
    active.started = true
  }
  const point = {
    x: (event.clientX - geometry.left) / geometry.scale,
    y: (event.clientY - geometry.top) / geometry.scale,
  }
  const next =
    active.kind === 'resize' && active.role
      ? applyResize(active.origin, active.role, point, event.shiftKey)
      : moveStroke(
          active.origin,
          point.x - active.start.x,
          point.y - active.start.y,
          natural.value.width,
          natural.value.height
        )
  strokes.value = strokes.value.map((stroke, index) => (index === active.index ? next : stroke))
  event.preventDefault()
  event.stopPropagation()
}

function sheetUp(event: PointerEvent) {
  const active = gesture
  if (!active || (gesturePointer !== null && event.pointerId !== gesturePointer)) return
  gesture = null
  gesturePointer = null
  interacting.value = false
  if (!active.started) return
  const moved = strokes.value[active.index]
  if (moved && isEmptyStroke(moved)) {
    strokes.value = strokes.value.filter((_, index) => index !== active.index)
    selectedStroke.value = null
    return
  }
  pushHistory(active.before)
}

/** 手势被打断（指针取消）：退回按下前的样子，不记历史。 */
function sheetCancel(event: PointerEvent) {
  const active = gesture
  if (!active || (gesturePointer !== null && event.pointerId !== gesturePointer)) return
  gesture = null
  gesturePointer = null
  interacting.value = false
  if (active.started) strokes.value = active.before
}

/** select 工具下双击文字进编辑。 */
function sheetDoubleClick(event: MouseEvent) {
  if (tool.value !== 'select') return
  const geometry = imageGeometry()
  if (!geometry) return
  const display = { x: event.clientX - geometry.left, y: event.clientY - geometry.top }
  const index = hitTest(strokes.value, display, { scale: geometry.scale, naturalWidth: natural.value.width })
  if (index === null) return
  if (strokes.value[index]?.tool !== 'text') return
  openTextEditor(index)
}

const editField = ref<HTMLInputElement | null>(null)
/** 编辑一条已有文字：原文字不画，由这个输入框呈现。 */
function openTextEditor(index: number) {
  const stroke = strokes.value[index]
  if (!stroke || stroke.tool !== 'text') return
  if (editingText.value?.index === index) return
  if (editingText.value) commitTextEdit()
  tool.value = 'select'
  selectedStroke.value = index
  editingText.value = { index, value: stroke.text }
  textEditing.value = true
  void nextTick(() => {
    editField.value?.focus()
    editField.value?.select()
  })
}
/** 提交文字编辑；trim 为空就把这条文字对象删掉。 */
function commitTextEdit() {
  const edit = editingText.value
  if (!edit) return
  editingText.value = null
  textEditing.value = false
  const before = strokes.value
  const stroke = before[edit.index]
  if (!stroke || stroke.tool !== 'text') return
  const text = edit.value.trim()
  if (!text) {
    strokes.value = before.filter((_, index) => index !== edit.index)
    pushHistory(before)
    selectedStroke.value = null
    return
  }
  if (text === stroke.text) return
  strokes.value = before.map((current, index) => (index === edit.index ? { ...stroke, text } : current))
  pushHistory(before)
}
/** 取消文字编辑：原文照旧留着。 */
function cancelTextEdit() {
  if (!editingText.value) return
  editingText.value = null
  textEditing.value = false
}
function onEditEnter(event: KeyboardEvent) {
  event.stopPropagation()
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  commitTextEdit()
}
function onEditEscape(event: KeyboardEvent) {
  event.stopPropagation()
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  cancelTextEdit()
}
const editStyle = computed(() => {
  const edit = editingText.value
  const stroke = edit ? strokes.value[edit.index] : null
  if (!edit || stroke?.tool !== 'text') return {}
  return {
    left: `${stroke.at.x * scale.value}px`,
    top: `${stroke.at.y * scale.value}px`,
    color: stroke.color,
    fontSize: `${fontSize(natural.value.width) * scale.value}px`,
  }
})
/** 正在画或正在拖的时候，撤销/重做要被拒绝。 */
const busy = computed(() => interacting.value || canvas.value?.busy === true)
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
    // 交出去了：屏上还留着，但记下发出去时是哪几笔——再走就不欠谁的了
    // （撤销再重做回到同一个样子也不算欠）。
    sentStrokes.value = [...strokes.value]
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
    if (editingText.value) cancelTextEdit()
    else canvas.value?.cancel()
    return true
  }
  if (tool.value === 'select') {
    // select 工具下手里还挑着一笔：Esc 放下它。
    if (selectedStroke.value !== null) {
      selectedStroke.value = null
      return true
    }
    return false
  }
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
    // 正在画、正在拖的时候，撤销/重做一律拒绝：手上这一笔还没落定，历史不好动。
    if (busy.value) return
    if (event.shiftKey ? canRedo.value : canUndo.value) {
      event.preventDefault()
      if (event.shiftKey) redo()
      else undo()
    }
    return
  }
  if (mod && event.key.toLowerCase() === 'y') {
    if (busy.value) return
    if (canRedo.value) {
      event.preventDefault()
      redo()
    }
    return
  }
  // Backspace / Delete 删掉选中的那一笔。
  if (event.key === 'Backspace' || event.key === 'Delete') {
    if (selectedStroke.value !== null) {
      event.preventDefault()
      deleteSelected()
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
/**
 * 走之前那一句确认（参考物写的是 `Discard your annotations?`）。
 *
 * 确认了就丢笔画，同时立刻把登记处撤掉——`WorkPanel` 那边「关文件」是「先确认、
 * 再换页签」，换页签时还会再问一次；这里同步撤掉，第二次才不会又弹一遍。
 */
function confirmDiscard(): Promise<boolean> {
  // 已经开着就复用那一次：多来几次导航，同一个答复答住，不各自挂一个悬着的 Promise。
  if (discardPending) return discardPending
  discardPending = new Promise<boolean>((resolve) => {
    resolveDiscard = resolve
  })
  discardOrigin.value = (pane.value?.ownerDocument.activeElement as HTMLElement | null) ?? null
  discardPrompt.value = true
  void nextTick(() => discardKeep.value?.focus({ preventScroll: true }))
  return discardPending
}
const discardGuard = { confirmDiscard }
function answerDiscard(go: boolean) {
  discardPrompt.value = false
  const resolve = resolveDiscard
  resolveDiscard = null
  discardPending = null
  const origin = discardOrigin.value
  discardOrigin.value = null
  // 关掉之后把焦点还回原来那个地方（还在的话）。
  if (origin?.isConnected) origin.focus({ preventScroll: true })
  if (go) {
    dropAnnotations()
    clearAnnotationGuard(discardGuard)
  }
  resolve?.(go)
}
/** 弹框里只有两颗按钮：Tab 到头绕回去，别让焦点漏到后面那份界面上。 */
function trapDiscardFocus(event: KeyboardEvent) {
  const card = discardCard.value
  if (!card) return
  const focusables = Array.from(card.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
  if (!focusables.length) return
  const at = focusables.indexOf(event.target as HTMLButtonElement)
  const next = event.shiftKey ? (at <= 0 ? focusables.length - 1 : at - 1) : at === focusables.length - 1 ? 0 : at + 1
  event.preventDefault()
  focusables[next]?.focus()
}
// 只有「此刻在看着的、且有没发出去的笔画」的那一块才登记：收起来的页签（v-show 留着
// 的）也在跑，但它们没在看，不该拦住别人。
// flush: 'sync' —— 这道登记是被同步读的（路由守卫、关页签、切文件都在同一拍里读它），
// 落在一拍之后的刷新队列里就等于「刚画完那一瞬间还没登记」。同步跑，登记和写笔画是同一拍。
watchEffect(
  () => {
    if (props.active && wouldLoseStrokes.value) setAnnotationGuard(discardGuard)
    else clearAnnotationGuard(discardGuard)
  },
  { flush: 'sync' }
)
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
  clearAnnotationGuard(discardGuard)
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
      :text-editing="textEditing"
      @pick="tool = $event"
      @recolor="recolor"
      @undo="undo"
      @redo="redo"
      @clear="clearStrokes"
      @send="sendAnnotated"
    />
    <output v-if="sendError" class="design-image__error" role="alert">{{ sendError }}</output>
    <output v-if="selectedRegion" class="t-meta design-image__region" aria-live="polite">{{
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
        <div
          class="design-image__sheet"
          :style="dimensions"
          @pointerdown.capture="sheetDown"
          @pointermove.capture="sheetMove"
          @pointerup.capture="sheetUp"
          @pointercancel.capture="sheetCancel"
          @dblclick.capture="sheetDoubleClick"
        >
          <img :key="`${identity}:${src}`" ref="image" :src="src" :alt="alt" draggable="false" @load="loaded" />
          <DesignSketchOverlay
            v-if="strokes.length"
            :strokes="strokes"
            :scale="scale"
            :natural-width="natural.width"
            :selected="selectedStroke"
            :editing="editingText ? editingText.index : null"
          />
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
          <!-- 对象级编辑的命中判定在 sheet 的 capture 这一层做（见 sheetDown）：
               点中笔画就进入编辑，点在空白处就把指针让给下面的区域选择器。 -->
          <input
            v-if="editingText"
            ref="editField"
            v-model="editingText.value"
            class="design-image__text"
            autocomplete="off"
            :style="editStyle"
            :placeholder="t('design.textPlaceholder')"
            @keydown.enter="onEditEnter"
            @keydown.esc="onEditEscape"
            @blur="commitTextEdit"
            @pointerdown.stop
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
            :strokes="strokes"
            @stroke="addStroke"
            @pick-block="pickBlock"
            @edit-text="openTextEditor"
            @text-editing="textEditing = $event"
          />
        </div>
      </div>
      <div class="design-image__overlay">
        <slot name="region-note" :geometry="geometry" :restore-focus="restoreFocus" :focus-origin="focusOrigin" />
      </div>
    </div>
    <!-- 要丢没发出去的标注之前先问一句；参考物的文案是「Discard your annotations?」。 -->
    <div
      v-if="discardPrompt"
      class="design-image__discard"
      role="alertdialog"
      aria-modal="true"
      :aria-label="t('design.discardAnnotations')"
      @keydown.esc="answerDiscard(false)"
      @keydown.tab="trapDiscardFocus"
    >
      <div ref="discardCard" class="design-image__discard-card">
        <p class="design-image__discard-question">{{ t('design.discardAnnotations') }}</p>
        <div class="design-image__discard-actions">
          <button ref="discardKeep" type="button" @click="answerDiscard(false)">{{ t('design.discardKeep') }}</button>
          <button type="button" class="is-danger" @click="answerDiscard(true)">{{ t('design.discardConfirm') }}</button>
        </div>
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
  /* 和标注工具栏同一条：窄面板里换行会占掉三四行，图就没地方了——排成一行，
     超出的横滚。缩放那几颗按钮本来就是固定宽度，横滚比换行好找。 */
  flex-wrap: nowrap;
  overflow-x: auto;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--line);
}
.design-image__error {
  padding: 6px 8px;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 「原图像素：x=…」这行在 240px 宽的面板里会折成两行、白吃掉 30 多像素，
   把图挤到面板外面去。它是状态行，一行放不下就省略，不要换行。 */
.design-image__region {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
/* 图上正在编辑的那条文字：虚线框的输入框，位置和字号跟着文字锚点走。 */
.design-image__text {
  position: absolute;
  min-width: 80px;
  border: 1px dashed var(--accent);
  background: var(--surface);
  font: inherit;
  padding: 1px 2px;
}
/* 丢弃确认：盖住这一格的一小块，不抢整页。 */
.design-image__discard {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: color-mix(in srgb, var(--surface) 72%, transparent);
}
.design-image__discard-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 320px;
  padding: 16px;
  background: var(--raised);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.design-image__discard-question {
  margin: 0;
  font-size: 14px;
}
.design-image__discard-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.design-image__discard-actions button {
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.design-image__discard-actions button:hover {
  background: var(--fill-2);
}
.design-image__discard-actions button.is-danger {
  color: var(--danger-ink);
}
.design-image__discard-actions button:focus-visible {
  outline: 2px solid var(--accent);
}
</style>
