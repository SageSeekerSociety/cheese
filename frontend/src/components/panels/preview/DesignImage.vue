<script setup lang="ts">
import type { ImageRegionMark, Point, RasterRegion, RasterSelection } from './designRegion'
import type { SketchTool } from './designSketch'

import { computed, ref, watch } from 'vue'

import { usePreviewMarking } from '@/composables/panelPreview/previewMode'

import DesignCropBox from './DesignCropBox.vue'
import DesignMinimap from './DesignMinimap.vue'
import DesignRasterRegion from './DesignRasterRegion.vue'
import { imagePoint } from './designRegion'
import { SKETCH_COLORS } from './designSketch'
import DesignSketchCanvas from './DesignSketchCanvas.vue'
import DesignSketchOverlay from './DesignSketchOverlay.vue'
import DesignSketchToolbar from './DesignSketchToolbar.vue'
import DesignViewToolbar from './DesignViewToolbar.vue'
import { useDesignAdjust } from './useDesignAdjust'
import { useDesignAnnotations } from './useDesignAnnotations'
import { useDesignDiscardGuard } from './useDesignDiscardGuard'
import { useDesignGif } from './useDesignGif'
import { useDesignInspect } from './useDesignInspect'
import { useDesignShortcuts } from './useDesignShortcuts'
import { useDesignViewport } from './useDesignViewport'

import { t } from '@/i18n'
import { takeImageTool } from '@/lib/chatImages'

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
    /** 图上框出来的那几处评论：一块一个点，点开就是那一串。
     *
     *  位置是**原图像素**（和框选发出去的那份引用同一套坐标），显示时按屏幕上的缩放
     *  换算——读者缩放、换窗口大小都不改变它指的原图上是哪一块。 */
    marks?: readonly ImageRegionMark[]
    /** 此刻打开着的那一串，画得实一点。 */
    activeMark?: string | null
    /** 这种格式可能带透明：图底下铺棋盘格，透明的地方才看得出是透明。 */
    transparent?: boolean
    /** 这一张的原始字节：是动图时用它逐帧解，交给浏览器自己放就只能看不能翻。 */
    gifBytes?: ArrayBuffer | null
    /**
     * 宿主给了这张图一块定高的地方（工作面板的预览格）。
     *
     * 给了才有「适应视口」这一档、打开时也落在这一档；嵌在一段按内容长高的正文里
     * （文件详情、版本对比）没有高可适应，只摆「适应宽度」和「实际像素」。
     */
    fitsHeight?: boolean
  }>(),
  {
    selectionEnabled: true,
    active: true,
    activeRegion: undefined,
    zoomOnWheel: false,
    marks: () => [],
    activeMark: null,
    transparent: false,
    gifBytes: null,
    fitsHeight: false,
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
  /** 点了一处评论标记：把那一串打开（宿主决定开哪一栏）。 */
  mark: [id: string]
}>()

const image = ref<HTMLImageElement | null>(null)
/** 画布只在用着会画的工具时挂着，所以这一格多半是空的——Esc 要作废手里的这一笔时用它。 */
const canvas = ref<InstanceType<typeof DesignSketchCanvas> | null>(null)
const natural = ref({ width: 0, height: 0 })
const standaloneRegion = ref<RasterRegion | null>(null)

const tool = ref<SketchTool>('select')
// 「在图上说」那一行只在批注模式里摆（没有模式的地方照旧摆）。切回「看」时笔收起来，
// 拖动只是在看图；画了没发的笔画还在，那一行留着，免得画好的东西没处可发。
const marking = usePreviewMarking()
watch(marking, (on) => {
  if (!on) tool.value = 'select'
})
const color = ref<string>(SKETCH_COLORS[0])
/** 亮度和饱和度那两根滑杆摊开着没有。 */
const tonePanel = ref(false)

const selectedRegion = computed(() => (props.activeRegion === undefined ? standaloneRegion.value : props.activeRegion))
/** 框选是默认工具：打开就能拖，不用先点一下按钮。 */
const selecting = computed(() => tool.value === 'select')
const drawing = computed(() => tool.value !== 'select')

/**
 * 看这张图那几件：旋转、翻转、裁剪、亮度和饱和度。
 *
 * 摆在视口那一份**前面**：视口一挂上就现算一次版面（`dimensions`），那次现算要问
 * `layoutCrop`（读者是不是正在重新裁），问到 `adjust`。后声明就先问到了一块还没建好的
 * 东西，一挂上就抛。
 *
 * `layoutCrop` / `frameClip` 也往后挪：`watch([dimensions, scale], …)` 在 setup 里就跑
 * 一次源取值，那条路今天被 `natural.width === 0` 的早退挡着才没走到，可那是个 `const`，
 * TDZ 说翻就翻——换成 ref 预置了宽高、或者早退被去掉，挂载时就 ReferenceError。
 */
const adjust = useDesignAdjust(() => props.identity)
/** 版面按哪一块缩：**重新裁的时候不缩**。 */
const layoutCrop = computed(() => (adjust.cropping.value ? null : adjust.adjust.value.crop))
/** 重新裁的时候不剪：框铺在整张上，读者框的就是他看见的那张。 */
const frameClip = computed(() => (adjust.cropping.value ? undefined : adjust.clip.value))

const {
  viewport,
  pane,
  available,
  scale,
  dimensions,
  spaceHeld,
  panning,
  narrow,
  setZoom,
  setZoomMode,
  zoomMode,
  scrollToRatio,
  scroll,
  fit,
  wheel,
  touchDown,
  touchMove,
  touchEnd,
  panStart,
  panMove,
  panEnd,
  blur,
} = useDesignViewport({
  zoomOnWheel: () => props.zoomOnWheel,
  natural,
  image,
  adjust: () => adjust.adjust.value,
  crop: () => layoutCrop.value,
  fitsHeight: () => props.fitsHeight,
})

/** 读这张图那两件：取色、量距离。 */
const inspect = useDesignInspect(() => image.value)
/** 动图：能翻帧就摆一层画布盖住浏览器自己放的动画。 */
const gif = useDesignGif(
  () => props.identity,
  () => props.gifBytes ?? null
)
const gifCanvas = gif.gifCanvas
/** 转 90°/270° 时屏幕上那张图的宽高和原图对调。 */
/** 明显比视口长的时候才摆定位条：铺得下的图摆一条只会碍事。 */
const showMinimap = computed(
  () => natural.value.width > 0 && scroll.value.height > 0 && scroll.value.content > scroll.value.height * 1.6
)
/** 图上的批注在整张里的比例位置（顶上那一行导航用）。 */
const markRatios = computed(() =>
  (props.marks ?? []).map((mark) => (natural.value.height ? mark.region.y / natural.value.height : 0))
)
const shownSize = computed(() =>
  adjust.turned.value
    ? { width: natural.value.height, height: natural.value.width }
    : { width: natural.value.width, height: natural.value.height }
)
const adjustValues = computed(() => ({ rotate: adjust.adjust.value.rotate, flip: adjust.adjust.value.flip }))
/**
 * 转动、调色、裁剪都作用在这一层上；里面的东西跟着一起转。
 *
 * 这一层按**原图的比例**摆（不是 sheet 那个转后的框）：图没被拉过，转过来才是对的形状。
 * 它钉在 sheet 正中，转轴也在正中——90° 之后占的正好是 sheet 那个宽高对调的框。
 */
const frameStyle = computed(() => {
  const crop = layoutCrop.value
  const shownWidth = shownSize.value.width * scale.value
  const shownHeight = shownSize.value.height * scale.value
  // 版面已经缩成裁完的那一块，这一层的中心就要跟着挪：原来的中心落在 (crop.x + w/2) 那一点。
  const left = crop ? shownWidth * (0.5 - crop.x) : shownWidth / 2
  const top = crop ? shownHeight * (0.5 - crop.y) : shownHeight / 2
  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${natural.value.width * scale.value}px`,
    height: `${natural.value.height * scale.value}px`,
    transform: `translate(-50%, -50%) rotate(${adjust.adjust.value.rotate}deg) scaleX(${adjust.adjust.value.flip ? -1 : 1})`,
    filter: adjust.filter.value,
    clipPath: frameClip.value,
  }
})

const regionIdentity = computed(() => `${props.identity}:${scale.value}:${available.value}`)
/** 原图像素 → 屏幕像素的比例：叠在图上那些（选中框、评论点）都按它换算。转过 90° 时
 *  `natural.width` 和屏幕上的宽对不上，所以不现推。 */
const pixelScale = computed(() => scale.value)
const selectedStyle = computed(() => {
  const region = selectedRegion.value
  return region
    ? {
        left: `${region.x * pixelScale.value}px`,
        top: `${region.y * pixelScale.value}px`,
        width: `${region.width * pixelScale.value}px`,
        height: `${region.height * pixelScale.value}px`,
      }
    : {}
})

// 图上那几处评论的标记点：一块一个点，落在那一块的左上角。位置是原图像素，按屏幕上的
// 缩放换算（和选中框同一条）；读者缩放、换窗口大小都不改变它指的那一块。
const markDots = computed(() =>
  (props.marks ?? []).map((mark) => ({
    id: mark.id,
    style: {
      left: `${mark.region.x * pixelScale.value}px`,
      top: `${mark.region.y * pixelScale.value}px`,
    },
  }))
)

const {
  strokes,
  selectedStroke,
  editingText,
  editField,
  canUndo,
  canRedo,
  busy,
  editStyle,
  addStroke,
  undo,
  redo,
  clearStrokes,
  dropAnnotations,
  recolor,
  restyle,
  redactStyle,
  redactToolbar,
  redactToolbarStyle,
  deleteSelected,
  cancelGesture,
  commitTextEdit,
  cancelTextEdit,
  openTextEditor,
  onEditEnter,
  onEditEscape,
  sheetDown,
  sheetMove,
  sheetUp,
  sheetCancel,
  sheetDoubleClick,
  note,
  profile,
  drawingText,
  textEditing,
  penWidth,
  canSend,
  exporting,
  sendError,
  wouldLoseStrokes,
  readProfile,
  pickBlock,
  sendAnnotated,
  reset: resetAnnotations,
} = useDesignAnnotations({
  image,
  natural,
  scale,
  tool,
  color,
  canvasBusy: () => canvas.value?.busy === true,
  alt: () => props.alt,
  adjust: () => adjust.adjust.value,
  emitAnnotate: (payload) => emit('annotate', payload),
})

/** 取色/量距离的手指落点换算：屏幕上这一点落在原图的哪个像素。 */
function inspectPoint(event: PointerEvent): Point | null {
  const element = image.value
  if (!element?.naturalWidth || !natural.value.width) return null
  const rect = element.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  return imagePoint(
    { x: event.clientX, y: event.clientY },
    {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      naturalWidth: element.naturalWidth,
      naturalHeight: element.naturalHeight,
      scale: scale.value,
      adjust: adjustValues.value,
    }
  )
}
let inspecting = false
function inspectDown(event: PointerEvent) {
  if (!inspect.active.value || event.button !== 0) return sheetDown(event)
  const at = inspectPoint(event)
  if (!at) return
  inspecting = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  if (inspect.mode.value === 'pick') inspect.pick(at)
  else inspect.startMeasure(at)
  event.preventDefault()
  event.stopPropagation()
}
function inspectMove(event: PointerEvent) {
  if (!inspecting) return sheetMove(event)
  if (inspect.mode.value !== 'measure') return
  const at = inspectPoint(event)
  if (at) inspect.dragMeasure(at)
  event.stopPropagation()
}
function inspectUp(event: PointerEvent) {
  if (!inspecting) return sheetUp(event)
  inspecting = false
  event.stopPropagation()
}

useDesignShortcuts({
  active: () => props.active,
  spaceHeld,
  tool,
  selectedStroke,
  editingText,
  textEditing,
  cancelTextEdit,
  cancelCanvas: () => canvas.value?.cancel(),
  busy,
  canUndo,
  canRedo,
  undo,
  redo,
  cancelGesture,
  deleteSelected,
  exitSelection: () => cancelSelection(),
})

const { discardPrompt, discardKeep, discardCard, answerDiscard, trapDiscardFocus } = useDesignDiscardGuard({
  active: () => props.active,
  wouldLoseStrokes: () => wouldLoseStrokes.value,
  dropAnnotations,
  pane,
})

function cancelSelection() {
  tool.value = 'select'
}
function loaded(event: Event) {
  const current = image.value
  if (!current || event.currentTarget !== current || current.getAttribute('src') !== props.src) return
  natural.value = { width: current.naturalWidth, height: current.naturalHeight }
  readProfile(current)
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

watch(
  [() => props.src, () => props.identity],
  () => {
    natural.value = { width: 0, height: 0 }
    standaloneRegion.value = null
    fit()
    resetAnnotations()
  },
  { flush: 'sync' }
)
watch(
  () => props.selectionEnabled,
  () => {
    standaloneRegion.value = null
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
// 从对话的看图浮层点「写字」「打码」「框选批注」交过来的：打开就是那件工具。
watch(
  () => props.identity,
  (identity) => {
    const handed = takeImageTool(identity)
    if (handed) tool.value = handed
  },
  { immediate: true }
)
// 收起来的那一刻把手里的东西放下：按着的空格、（真在拖的话）正在进行的平移。
watch(
  () => props.active,
  (on) => {
    if (!on) blur()
  }
)
</script>

<template>
  <section class="design-image">
    <DesignViewToolbar
      :scale="scale"
      :mode="zoomMode"
      :fits-height="fitsHeight"
      :adjust="adjust.adjust.value"
      :edited="adjust.edited.value"
      :cropping="adjust.cropping.value"
      :tone-open="tonePanel"
      @zoom="setZoom(scale * $event)"
      @mode="setZoomMode($event)"
      @rotate="adjust.rotate()"
      @flip="adjust.flip()"
      @crop="adjust.startCrop()"
      @tone="tonePanel = $event"
      @restore="adjust.restore()"
    >
      <template #actions><slot name="actions" /></template>
    </DesignViewToolbar>
    <DesignSketchToolbar
      v-if="marking || strokes.length > 0"
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
      :redact="redactToolbar"
      :redact-style="redactToolbarStyle"
      @pick="tool = $event"
      @recolor="recolor"
      @restyle="restyle"
      @undo="undo"
      @redo="redo"
      @clear="clearStrokes"
      @send="sendAnnotated"
    />
    <!-- Status lines, not a form's result: a <p> with the live-region role says so, where <output> would want a label. -->
    <p v-if="sendError" class="design-image__error" role="alert">{{ sendError }}</p>
    <!-- 没选的时候也占着这一行：选中一块时这行要是才长出来，下面的图格就矮一截，「适应视口」
         跟着把图缩一档，刚框好的那块和旁边的说明卡都挪了位。 -->
    <p class="t-meta design-image__region" role="status">
      <template v-if="selectedRegion">{{ t('design.selectedRegion', selectedRegion) }}</template>
    </p>
    <div
      ref="viewport"
      class="design-image__viewport"
      :class="{ 'is-pannable': spaceHeld, 'is-panning': panning }"
      @pointerdown.capture="panStart"
      @pointermove.capture="panMove"
      @pointerup.capture="panEnd"
      @pointercancel.capture="panEnd"
    >
      <div
        ref="pane"
        class="design-image__pane"
        :class="{ 'is-narrow': narrow }"
        @wheel="wheel"
        @pointerdown.capture="touchDown"
        @pointermove.capture="touchMove"
        @pointerup.capture="touchEnd"
        @pointercancel.capture="touchEnd"
      >
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- double-clicking a text mark reopens it for editing, part of drawing with the pointer; the image has no keyboard drawing mode -->
        <div
          class="design-image__sheet"
          :style="dimensions"
          @pointerdown.capture="inspectDown"
          @pointermove.capture="inspectMove"
          @pointerup.capture="inspectUp"
          @pointercancel.capture="sheetCancel"
          @lostpointercapture.capture="sheetCancel"
          @dblclick.capture="sheetDoubleClick"
        >
          <!-- 转动、调色、裁剪都作用在这一层上，里面的东西（画好的笔画、选中的那块、评论点、
               正在画的画布）跟着一起转——它们在数据里都是原图像素，谁也不需要各自再转一次。 -->
          <div class="design-image__frame" :class="{ 'is-transparent': props.transparent }" :style="frameStyle">
            <img
              :key="`${identity}:${src}`"
              ref="image"
              :src="src"
              :alt="alt"
              decoding="async"
              draggable="false"
              @load="loaded"
            />
            <!-- 动图暂停时：这一帧画在画布上盖住浏览器自己放的动画，翻帧就是换画布内容。 -->
            <canvas
              v-show="gif.gif.value.available && gif.gif.value.paused"
              ref="gifCanvas"
              class="design-image__gif"
            />
            <DesignSketchOverlay
              v-if="strokes.length"
              :strokes="strokes"
              :scale="scale"
              :natural-width="natural.width"
              :selected="selectedStroke"
              :editing="editingText ? editingText.index : null"
            />
            <div v-if="selectedRegion" class="design-image__selection" :style="selectedStyle" aria-hidden="true" />
            <DesignRasterRegion
              :image="image"
              :enabled="selecting && selectionEnabled && !textEditing && !adjust.cropping.value"
              :identity="regionIdentity"
              :profile="profile"
              :adjust="adjustValues"
              @select="selected"
              @cancel="cancelSelection"
            />
            <!-- 图上框出来的那几处评论：一块一个点，点开就是那一串。和选中框一样按原图像素
               定位，所以缩放和改窗口大小之后仍然落在那一块上。
               必须排在框选层**之后**：几层都是 `position: absolute` 的兄弟，靠后的画在上面
               也先接到指针。排在前面时，框选层那一整张（`inset: 0`）压在点上、还带着自己的
               按下处理，真鼠标就点在框选层上，点事件到不了这颗按钮——单测把点击直接打在
               元素上，正好绕过这一点。改文字那个输入框仍在更后面，编辑时它在上层。 -->
            <button
              v-for="dot in markDots"
              :key="dot.id"
              type="button"
              class="design-image__mark"
              :class="{ 'is-active': dot.id === activeMark }"
              :style="dot.style"
              :data-comment-mark="dot.id"
              :aria-label="t('work.room.comments.commentHere')"
              :title="t('work.room.comments.commentHere')"
              @click.stop="emit('mark', dot.id)"
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
              :redact-style="redactStyle"
              :profile="profile"
              :strokes="strokes"
              :adjust="adjustValues"
              @stroke="addStroke"
              @pick-block="pickBlock"
              @edit-text="openTextEditor"
              @text-editing="drawingText = $event"
            />
            <!-- 量出来的那条线画在图上：两端都在原图坐标里，所以它得挂在**图这一层**上，
                 跟着图一起转、一起翻、一起裁——挂在外面那层的话，一滚动或一转就指错地方。
                 坐标原单位是原图像素，viewBox 按原图宽高铺开，正好一一对上。 -->
            <svg
              v-if="inspect.measure.value && natural.width"
              class="design-image__measure"
              :viewBox="`0 0 ${natural.width} ${natural.height}`"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <line
                :x1="inspect.measure.value.from.x"
                :y1="inspect.measure.value.from.y"
                :x2="inspect.measure.value.to.x"
                :y2="inspect.measure.value.to.y"
                stroke="var(--accent)"
                stroke-width="2"
                stroke-dasharray="6 4"
                vector-effect="non-scaling-stroke"
              />
            </svg>
          </div>
          <!-- 裁剪框铺在转好调好的那张图上（不是 sheet 上）：它框的是「留下哪一块」，量的
               就是读者看到的样子。 -->
          <DesignCropBox
            v-if="adjust.cropping.value"
            v-model="adjust.draftCrop.value"
            :width="shownSize.width * scale"
            :height="shownSize.height * scale"
          />
        </div>
      </div>
      <div class="design-image__inspect" role="group" :aria-label="t('design.inspect')">
        <button
          type="button"
          class="design-image__inspect-btn"
          :class="{ 'is-on': inspect.mode.value === 'pick' }"
          :aria-pressed="inspect.mode.value === 'pick'"
          :title="t('design.pickHint')"
          @click="inspect.setMode('pick')"
        >
          <i class="mdi mdi-eyedropper-variant" aria-hidden="true" />{{ t('design.pick') }}
        </button>
        <button
          type="button"
          class="design-image__inspect-btn"
          :class="{ 'is-on': inspect.mode.value === 'measure' }"
          :aria-pressed="inspect.mode.value === 'measure'"
          :title="t('design.measureHint')"
          @click="inspect.setMode('measure')"
        >
          <i class="mdi mdi-ruler" aria-hidden="true" />{{ t('design.measure') }}
        </button>
      </div>
      <DesignMinimap
        v-if="showMinimap && !adjust.cropping.value"
        :scroll-top="scroll.top"
        :viewport-height="scroll.height"
        :content-height="scroll.content"
        :marks="markRatios"
        @jump="scrollToRatio"
      />
      <!-- 动图：暂停/播放、前后翻帧。只有能逐帧解出来时才摆。 -->
      <div v-if="gif.gif.value.available" class="design-image__gifbar" role="group" :aria-label="t('design.gifFrames')">
        <button type="button" @click="gif.gif.value.paused ? gif.play() : gif.pause()">
          {{ gif.gif.value.paused ? t('design.gifPlay') : t('design.gifPause') }}
        </button>
        <button type="button" @click="gif.step(-1)">{{ t('design.gifPrev') }}</button>
        <span class="design-image__gifbar-count">
          {{ t('design.gifAt', { n: gif.gif.value.index + 1, total: gif.gif.value.count }) }}
        </span>
        <button type="button" @click="gif.step(1)">{{ t('design.gifNext') }}</button>
      </div>
      <!-- 取色/量距离读出来的那个数：浮在左下，别挡住图。 -->
      <div v-if="inspect.color.value || inspect.measure.value" class="design-image__reading" role="status">
        <template v-if="inspect.color.value">
          <span class="design-image__reading-swatch" :style="{ background: inspect.color.value.hex }" />
          <code>{{ inspect.color.value.hex }}</code>
          <span class="design-image__reading-where"
            >x {{ inspect.color.value.at.x }}、y {{ inspect.color.value.at.y }}</span
          >
        </template>
        <template v-else-if="inspect.measure.value">
          <code>{{ t('design.measured', { length: Math.round(inspect.measure.value.length) }) }}</code>
          <span class="design-image__reading-where">
            x {{ Math.round(inspect.measure.value.from.x) }}、y {{ Math.round(inspect.measure.value.from.y) }} → x
            {{ Math.round(inspect.measure.value.to.x) }}、y {{ Math.round(inspect.measure.value.to.y) }}
          </span>
        </template>
      </div>
    </div>
    <!-- 亮度饱和度两根滑杆：收在「调色」后面，摊开时浮在这一格右上。 -->
    <div v-if="tonePanel" class="design-image__tone">
      <label v-for="key in ['brightness', 'saturation'] as const" :key="key" class="design-image__tone-row">
        <span>{{ t(`design.tone.${key}`) }}</span>
        <input
          type="range"
          :min="key === 'brightness' ? 50 : 0"
          :max="key === 'brightness' ? 150 : 200"
          :value="adjust.adjust.value[key]"
          @input="adjust.change({ [key]: Number(($event.target as HTMLInputElement).value) })"
        />
        <span class="design-image__tone-value">{{ adjust.adjust.value[key] }}%</span>
      </label>
      <button
        type="button"
        class="design-image__tone-reset"
        @click="adjust.change({ brightness: 100, saturation: 100 })"
      >
        {{ t('design.toneReset') }}
      </button>
    </div>
    <!-- 裁剪中，底下一条确定/取消；回车也确定。 -->
    <div v-if="adjust.cropping.value" class="design-image__cropbar" role="group" :aria-label="t('design.crop')">
      <span class="design-image__cropbar-hint">{{ t('design.cropHintPanel') }}</span>
      <button type="button" @click="adjust.finishCrop(false)">{{ t('design.cancel') }}</button>
      <button type="button" class="is-primary" @click="adjust.finishCrop(true)">{{ t('design.cropApply') }}</button>
    </div>
    <!-- 要丢没发出去的标注之前先问一句；参考物的文案是「Discard your annotations?」。 -->
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the ARIA modal dialog pattern: Escape answers Keep and Tab stays inside, caught as they bubble from its two buttons -->
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

<style scoped src="./design-image.css"></style>
