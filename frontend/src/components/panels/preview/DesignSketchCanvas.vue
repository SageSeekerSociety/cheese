<script setup lang="ts">
import type { ImageGeometry, Point, RasterRegion } from './designRegion'
import type { SketchStroke, SketchTool, TextStroke } from './designSketch'
import type { ContentProfile } from './designSnap'

import { computed, nextTick, ref, watch } from 'vue'

import { imagePoint, imageRegion } from './designRegion'
import { arrowHead, fontSize, isShapeStroke } from './designSketch'
import { snapRegion } from './designSnap'

import { t } from '@/i18n'

const props = defineProps<{
  image: HTMLImageElement | null
  identity: string
  tool: SketchTool
  color: string
  width: number
  profile?: ContentProfile | null
}>()
const emit = defineEmits<{
  stroke: [stroke: SketchStroke]
  'pick-block': [point: Point]
  'text-editing': [editing: boolean]
}>()
const start = ref<Point | null>(null)
/** 正在拖、还没撒手的那一笔。文字不经过这里：它有输入框，撒手即成品。 */
const draft = ref<Exclude<SketchStroke, TextStroke> | null>(null)
const geometry = ref<ImageGeometry | null>(null)
const textAt = ref<Point | null>(null)
const textValue = ref('')
const textField = ref<HTMLInputElement | null>(null)
let pointer: number | null = null
let captured: { src: string; naturalWidth: number; naturalHeight: number } | null = null

function measure(): boolean {
  const image = props.image
  if (!image?.complete) return false
  const rect = image.getBoundingClientRect()
  if (!image.naturalWidth || !image.naturalHeight || !rect.width || !rect.height) return false
  geometry.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  }
  captured = {
    src: image.getAttribute('src') ?? '',
    naturalWidth: image.naturalWidth,
    naturalHeight: image.naturalHeight,
  }
  return true
}
function reset() {
  start.value = null
  draft.value = null
  geometry.value = null
  pointer = null
  captured = null
}
function cancelText() {
  if (!textAt.value) return
  textAt.value = null
  textValue.value = ''
  emit('text-editing', false)
}
function cancel() {
  reset()
  cancelText()
}
/** 组字中的回车是「选词」、Esc 是「取消候选」，都归输入法：别当成提交/放弃。 */
function onTextEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  commitText()
}
function onTextEscape(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  cancelText()
}
defineExpose({ cancel })

const scale = computed(() => {
  const size = geometry.value
  return size && size.naturalWidth ? size.width / size.naturalWidth : 1
})
const naturalWidth = computed(() => geometry.value?.naturalWidth ?? 0)
const displayed = computed(() => {
  const stroke = draft.value
  if (!stroke || !geometry.value) return null
  const toDisplay = (point: Point) => ({ x: point.x * scale.value, y: point.y * scale.value })
  const box = (region: RasterRegion) => ({
    x: region.x * scale.value,
    y: region.y * scale.value,
    width: region.width * scale.value,
    height: region.height * scale.value,
  })
  if (stroke.tool === 'pen')
    return { kind: 'pen' as const, points: stroke.points.map(toDisplay), width: stroke.width * scale.value }
  if (isShapeStroke(stroke)) return { kind: stroke.tool, box: box(stroke.region), width: stroke.width * scale.value }
  return {
    kind: stroke.tool,
    from: toDisplay(stroke.from),
    to: toDisplay(stroke.to),
    width: stroke.width * scale.value,
    head: arrowHead(stroke.from, stroke.to, stroke.width).map(toDisplay),
  }
})

function regionBetween(a: Point, b: Point): RasterRegion | null {
  const size = geometry.value
  if (!size) return null
  const region = imageRegion(a, b, size)
  if (!region) return null
  return props.profile ? snapRegion(region, props.profile) : region
}

function down(event: PointerEvent) {
  // 中键留给视口平移，右键留给浏览器菜单。
  if (event.button !== 0) return
  // 框选不落在这块画布上：工具是 select 时外层挂的是区域选择器，这块根本不渲染。
  // 挡一下，下面几支就只剩「会画东西」的工具。
  if (props.tool === 'select') return
  if (!measure()) return
  const size = geometry.value
  if (!size) return
  const point = imagePoint({ x: event.clientX, y: event.clientY }, size)
  if (!point) return
  if (props.tool === 'text') {
    textAt.value = point
    textValue.value = ''
    emit('text-editing', true)
    void nextTick(() => textField.value?.focus())
    event.preventDefault()
    return
  }
  start.value = { x: event.clientX, y: event.clientY }
  pointer = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  if (props.tool === 'pen') draft.value = { tool: 'pen', color: props.color, width: props.width, points: [point] }
  else if (props.tool === 'line' || props.tool === 'arrow')
    draft.value = { tool: props.tool, color: props.color, width: props.width, from: point, to: point }
  else
    draft.value = {
      tool: props.tool,
      color: props.color,
      width: props.width,
      region: { x: point.x, y: point.y, width: 0, height: 0 },
    }
  event.preventDefault()
}

function move(event: PointerEvent) {
  if (event.pointerId !== pointer || !start.value || !geometry.value || !draft.value) return
  const stroke = draft.value
  const point = imagePoint({ x: event.clientX, y: event.clientY }, geometry.value)
  if (!point) return
  if (stroke.tool === 'pen') stroke.points.push(point)
  else if (stroke.tool === 'line' || stroke.tool === 'arrow') stroke.to = point
  else if (isShapeStroke(stroke)) {
    const region = regionBetween(start.value, { x: event.clientX, y: event.clientY })
    if (region) stroke.region = region
  }
  draft.value = { ...stroke }
}

function up(event: PointerEvent) {
  if (event.pointerId !== pointer) return
  move(event)
  const stroke = draft.value
  const holding = start.value
  const size = geometry.value
  const context = captured
  const image = props.image
  const intact =
    !!size &&
    !!context &&
    !!image &&
    context.src === (image.getAttribute('src') ?? '') &&
    context.naturalWidth === image.naturalWidth &&
    context.naturalHeight === image.naturalHeight
  reset()
  if (!intact || !stroke || !size || !holding) return
  const tap = Math.hypot(event.clientX - holding.x, event.clientY - holding.y) < 4
  const point = imagePoint({ x: event.clientX, y: event.clientY }, size)
  if (tap && stroke.tool !== 'pen' && point) {
    // 点一下：不画东西，把光标下那一块内容选出来。
    if (stroke.tool === 'rect' || stroke.tool === 'ellipse' || stroke.tool === 'redact') emit('pick-block', point)
    return
  }
  if (stroke.tool === 'pen') {
    if (stroke.points.length >= 2) emit('stroke', stroke)
    return
  }
  if (stroke.tool === 'line' || stroke.tool === 'arrow') {
    if (Math.hypot(stroke.to.x - stroke.from.x, stroke.to.y - stroke.from.y) >= 2) emit('stroke', stroke)
    return
  }
  if (!isShapeStroke(stroke)) return
  if (stroke.region.width >= 2 && stroke.region.height >= 2) emit('stroke', stroke)
}

function commitText() {
  const point = textAt.value
  const text = textValue.value.trim()
  if (!point) return cancelText()
  textAt.value = null
  textValue.value = ''
  emit('text-editing', false)
  if (text) emit('stroke', { tool: 'text', color: props.color, width: props.width, at: point, text })
}

watch(
  [() => props.identity, () => props.image, () => props.tool],
  () => {
    cancel()
  },
  { flush: 'sync' }
)

const textStyle = computed(() => {
  const point = textAt.value
  if (!point) return {}
  return {
    left: `${point.x * scale.value}px`,
    top: `${point.y * scale.value}px`,
    color: props.color,
    fontSize: `${fontSize(naturalWidth.value) * scale.value}px`,
  }
})
</script>

<template>
  <div
    class="sketch-layer"
    tabindex="0"
    role="application"
    :aria-label="t('design.sketchLayer')"
    @pointerdown="down"
    @pointermove="move"
    @pointerup="up"
    @pointercancel="reset"
    @lostpointercapture="reset"
    @keydown.esc.prevent="cancel"
  >
    <svg v-if="displayed" class="sketch-layer__draft" aria-hidden="true">
      <polyline
        v-if="displayed.kind === 'pen'"
        :points="displayed.points.map((point) => `${point.x},${point.y}`).join(' ')"
        fill="none"
        :stroke="color"
        :stroke-width="displayed.width"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <g v-else-if="displayed.kind === 'line' || displayed.kind === 'arrow'">
        <line
          :x1="displayed.from.x"
          :y1="displayed.from.y"
          :x2="displayed.to.x"
          :y2="displayed.to.y"
          :stroke="color"
          :stroke-width="displayed.width"
          stroke-linecap="round"
        />
        <polygon
          v-if="displayed.kind === 'arrow'"
          :points="displayed.head.map((point) => `${point.x},${point.y}`).join(' ')"
          :fill="color"
        />
      </g>
      <rect
        v-else-if="displayed.kind === 'rect' || displayed.kind === 'redact'"
        :x="displayed.box.x"
        :y="displayed.box.y"
        :width="displayed.box.width"
        :height="displayed.box.height"
        :fill="displayed.kind === 'redact' ? color : 'none'"
        :stroke="color"
        :stroke-width="displayed.width"
      />
      <ellipse
        v-else-if="displayed.kind === 'ellipse'"
        :cx="displayed.box.x + displayed.box.width / 2"
        :cy="displayed.box.y + displayed.box.height / 2"
        :rx="displayed.box.width / 2"
        :ry="displayed.box.height / 2"
        fill="none"
        :stroke="color"
        :stroke-width="displayed.width"
      />
    </svg>
    <input
      v-if="textAt"
      ref="textField"
      v-model="textValue"
      class="sketch-layer__text"
      autocomplete="off"
      :style="textStyle"
      :placeholder="t('design.textPlaceholder')"
      @keydown.enter="onTextEnter"
      @keydown.esc="onTextEscape"
      @blur="commitText"
      @pointerdown.stop
    />
  </div>
</template>

<style scoped>
.sketch-layer {
  position: absolute;
  inset: 0;
  cursor: crosshair;
  touch-action: none;
}
.sketch-layer:focus-visible {
  outline: 2px solid var(--accent);
}
.sketch-layer__draft {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.sketch-layer__text {
  position: absolute;
  min-width: 80px;
  border: 1px dashed var(--accent);
  background: var(--surface, #fff);
  font: inherit;
  padding: 1px 2px;
}
</style>
