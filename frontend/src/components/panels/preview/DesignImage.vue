<script setup lang="ts">
import type { ImageRegionMark, RasterRegion, RasterSelection } from './designRegion'
import type { SketchTool } from './designSketch'

import { computed, ref, watch } from 'vue'

import DesignRasterRegion from './DesignRasterRegion.vue'
import { SKETCH_COLORS } from './designSketch'
import DesignSketchCanvas from './DesignSketchCanvas.vue'
import DesignSketchOverlay from './DesignSketchOverlay.vue'
import DesignSketchToolbar from './DesignSketchToolbar.vue'
import { useDesignAnnotations } from './useDesignAnnotations'
import { useDesignDiscardGuard } from './useDesignDiscardGuard'
import { useDesignShortcuts } from './useDesignShortcuts'
import { useDesignViewport } from './useDesignViewport'

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
    /** 图上框出来的那几处评论：一块一个点，点开就是那一串。
     *
     *  位置是**原图像素**（和框选发出去的那份引用同一套坐标），显示时按屏幕上的缩放
     *  换算——读者缩放、换窗口大小都不改变它指的原图上是哪一块。 */
    marks?: readonly ImageRegionMark[]
    /** 此刻打开着的那一串，画得实一点。 */
    activeMark?: string | null
  }>(),
  {
    selectionEnabled: true,
    active: true,
    activeRegion: undefined,
    zoomOnWheel: false,
    marks: () => [],
    activeMark: null,
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
const toolbar = ref<InstanceType<typeof DesignSketchToolbar> | null>(null)
/** 画布只在用着会画的工具时挂着，所以这一格多半是空的——Esc 要作废手里的这一笔时用它。 */
const canvas = ref<InstanceType<typeof DesignSketchCanvas> | null>(null)
const focusOrigin = ref<Element | null>(null)
const natural = ref({ width: 0, height: 0 })
const standaloneRegion = ref<RasterRegion | null>(null)
const selectedBox = ref<HTMLElement | null>(null)

const tool = ref<SketchTool>('select')
const color = ref<string>(SKETCH_COLORS[0])

const selectedRegion = computed(() => (props.activeRegion === undefined ? standaloneRegion.value : props.activeRegion))
/** 框选是默认工具：打开就能拖，不用先点一下按钮。 */
const selecting = computed(() => tool.value === 'select')
const drawing = computed(() => tool.value !== 'select')

const {
  viewport,
  pane,
  available,
  scale,
  fitted,
  dimensions,
  geometry,
  spaceHeld,
  panning,
  setZoom,
  fit,
  wheel,
  panStart,
  panMove,
  panEnd,
  blur,
  scheduleMeasure,
} = useDesignViewport({
  zoomOnWheel: () => props.zoomOnWheel,
  natural,
  image,
  selectedBox,
  selectedRegion,
  beforePanStart: () => rememberFocus(),
})

const regionIdentity = computed(() => `${props.identity}:${scale.value}:${available.value}`)
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

// 图上那几处评论的标记点：一块一个点，落在那一块的左上角。位置是原图像素，按屏幕上的
// 缩放换算（和选中框同一条）；读者缩放、换窗口大小都不改变它指的那一块。
const markDots = computed(() =>
  (props.marks ?? []).map((mark) => ({
    id: mark.id,
    style: {
      left: `${mark.region.x * scale.value}px`,
      top: `${mark.region.y * scale.value}px`,
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
  emitAnnotate: (payload) => emit('annotate', payload),
})

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
function rememberFocus() {
  if (selecting.value) focusOrigin.value = pane.value?.ownerDocument.activeElement ?? null
}
function restoreFocus(resourceKey: string) {
  const button = toolbar.value?.selectButton
  if (!button?.isConnected || button.disabled || resourceKey !== props.identity) return
  const document = button.ownerDocument
  if (!document.hasFocus() || (document.activeElement && document.activeElement !== document.body)) return
  button.focus({ preventScroll: true })
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
    geometry.value = null
    fit()
    resetAnnotations()
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
      <button type="button" :aria-pressed="fitted" @click="fit()">{{ t('design.fit') }}</button>
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
    <p v-if="selectedRegion" class="t-meta design-image__region" role="status">
      {{ t('design.selectedRegion', selectedRegion) }}
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
      <div ref="pane" class="design-image__pane" @wheel="wheel">
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- double-clicking a text mark reopens it for editing, part of drawing with the pointer; the image has no keyboard drawing mode -->
        <div
          class="design-image__sheet"
          :style="dimensions"
          @pointerdown.capture="sheetDown"
          @pointermove.capture="sheetMove"
          @pointerup.capture="sheetUp"
          @pointercancel.capture="sheetCancel"
          @lostpointercapture.capture="sheetCancel"
          @dblclick.capture="sheetDoubleClick"
        >
          <img
            :key="`${identity}:${src}`"
            ref="image"
            :src="src"
            :alt="alt"
            decoding="async"
            draggable="false"
            @load="loaded"
          />
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
          <!-- 图上框出来的那几处评论：一块一个点，点开就是那一串。和选中框一样按原图像素
               定位，所以缩放和改窗口大小之后仍然落在那一块上。 -->
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
          <DesignRasterRegion
            :image="image"
            :enabled="selecting && selectionEnabled && !textEditing"
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
            :redact-style="redactStyle"
            :profile="profile"
            :strokes="strokes"
            @stroke="addStroke"
            @pick-block="pickBlock"
            @edit-text="openTextEditor"
            @text-editing="drawingText = $event"
          />
        </div>
      </div>
      <div class="design-image__overlay">
        <slot name="region-note" :geometry="geometry" :restore-focus="restoreFocus" :focus-origin="focusOrigin" />
      </div>
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
