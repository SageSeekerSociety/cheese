<script setup lang="ts">
import type { Point, RasterRegion } from './designRegion'
import type { ShapeStroke, SketchStroke, TextStroke } from './designSketch'
import type { HandleRole } from './designSketchSelection'

import { computed, ref } from 'vue'

import {
  canResize,
  HANDLE_HIT_RADIUS,
  HANDLE_RADIUS,
  handlePoints,
  isSelectableStroke,
  moveRegion,
  nearestHandle,
  resizeRegion,
  SELECT_DASH,
  SELECT_STROKE_WIDTH,
  strokeBox,
} from './designSketchSelection'

/**
 * 已画好的块状标注的选中层：虚线框、八个把手、以及「点哪儿抓哪儿」。
 *
 * 只负责交互，不改数据：选中哪一条、框变成了什么样，都发出去交给 `DesignImage`。坐标
 * 一律先按 `scale` 换算成屏幕像素再量——框是给人抓的，得和屏幕上的像素一一对应，缩放
 * 时不能跟着图变大变小。
 */
const props = defineProps<{
  strokes: readonly SketchStroke[]
  selected: number | null
  scale: number
  naturalWidth: number
}>()
const emit = defineEmits<{
  select: [index: number]
  deselect: []
  resize: [{ index: number; box: RasterRegion }]
}>()

const root = ref<HTMLElement | null>(null)
const dash = SELECT_DASH.join(' ')
let pointerId: number | null = null
let dragging: { role: HandleRole; from: Point; origin: RasterRegion; index: number; movable: boolean } | null = null

function scaled(region: RasterRegion): RasterRegion {
  return {
    x: region.x * props.scale,
    y: region.y * props.scale,
    width: region.width * props.scale,
    height: region.height * props.scale,
  }
}

/** 每一条能被选中的标注：盒子（原图像素）+ 画哪种轮廓。 */
const pickable = computed(() =>
  props.strokes.flatMap((stroke, index) => {
    if (!isSelectableStroke(stroke)) return []
    const box = scaled(strokeBox(stroke, props.naturalWidth))
    const kind: 'rect' | 'ellipse' = stroke.tool === 'ellipse' ? 'ellipse' : 'rect'
    return [{ index, kind, box }]
  })
)

const selectedStroke = computed<ShapeStroke | TextStroke | null>(() => {
  const index = props.selected
  if (index === null) return null
  const stroke = props.strokes[index]
  return stroke && isSelectableStroke(stroke) ? stroke : null
})
const selectedBox = computed<RasterRegion | null>(() => {
  const stroke = selectedStroke.value
  return stroke ? scaled(strokeBox(stroke, props.naturalWidth)) : null
})
const handles = computed(() => (selectedBox.value ? handlePoints(selectedBox.value) : []))

function localPoint(event: PointerEvent): Point | null {
  const rect = root.value?.getBoundingClientRect()
  if (!rect) return null
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}
function toImage(region: RasterRegion): RasterRegion {
  if (!props.scale) return region
  return {
    x: region.x / props.scale,
    y: region.y / props.scale,
    width: region.width / props.scale,
    height: region.height / props.scale,
  }
}
function pick(index: number, event: PointerEvent) {
  event.stopPropagation()
  event.preventDefault()
  emit('select', index)
}
function grabDown(event: PointerEvent) {
  if (event.button !== 0) return
  const index = props.selected
  const box = selectedBox.value
  const stroke = selectedStroke.value
  const point = localPoint(event)
  if (index === null || !box || !stroke || !point) return
  // 这一层盖在区域选择器上面，抓取只认把手：抓住了就自己处理，抓空了只是取消选中。
  event.stopPropagation()
  event.preventDefault()
  const role = nearestHandle(box, point, HANDLE_HIT_RADIUS)
  if (!role) {
    emit('deselect')
    return
  }
  dragging = { role, from: point, origin: box, index, movable: !canResize(stroke) }
  pointerId = event.pointerId
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}
function grabMove(event: PointerEvent) {
  if (!dragging || event.pointerId !== pointerId) return
  const point = localPoint(event)
  if (!point) return
  const next = dragging.movable
    ? moveRegion(dragging.origin, dragging.from, point)
    : resizeRegion(dragging.origin, dragging.role, point)
  emit('resize', { index: dragging.index, box: toImage(next) })
}
function grabEnd(event: PointerEvent) {
  if (pointerId !== null && event.pointerId !== pointerId) return
  dragging = null
  pointerId = null
}
</script>

<template>
  <div ref="root" class="sketch-selection">
    <svg class="sketch-selection__svg" aria-hidden="true">
      <!-- 还没选中任何一条：每条块状标注画一圈透明轮廓，点中它就是选中它。 -->
      <template v-if="selected === null">
        <template v-for="item in pickable" :key="item.index">
          <ellipse
            v-if="item.kind === 'ellipse'"
            class="sketch-selection__pick"
            :cx="item.box.x + item.box.width / 2"
            :cy="item.box.y + item.box.height / 2"
            :rx="item.box.width / 2"
            :ry="item.box.height / 2"
            fill="none"
            stroke="transparent"
            @pointerdown="pick(item.index, $event)"
          />
          <rect
            v-else
            class="sketch-selection__pick"
            :x="item.box.x"
            :y="item.box.y"
            :width="item.box.width"
            :height="item.box.height"
            fill="none"
            stroke="transparent"
            @pointerdown="pick(item.index, $event)"
          />
        </template>
      </template>
      <!-- 选中了：虚线框 + 八个把手。 -->
      <template v-else-if="selectedBox">
        <rect
          class="sketch-selection__box"
          :x="selectedBox.x"
          :y="selectedBox.y"
          :width="selectedBox.width"
          :height="selectedBox.height"
          fill="none"
          :stroke-width="SELECT_STROKE_WIDTH"
          :stroke-dasharray="dash"
        />
        <circle
          v-for="handle in handles"
          :key="handle.role"
          class="sketch-selection__handle"
          :cx="handle.x"
          :cy="handle.y"
          :r="HANDLE_RADIUS"
        />
      </template>
    </svg>
    <div
      v-if="selected !== null"
      class="sketch-selection__grab"
      @pointerdown="grabDown"
      @pointermove="grabMove"
      @pointerup="grabEnd"
      @pointercancel="grabEnd"
      @lostpointercapture="grabEnd"
    />
  </div>
</template>

<style scoped>
.sketch-selection {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.sketch-selection__svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
}
/* 只在轮廓那几像素上接事件：透过它下面的区域选择器照旧能用。 */
.sketch-selection__pick {
  stroke-width: 12;
  pointer-events: stroke;
  cursor: pointer;
}
.sketch-selection__box {
  stroke: var(--accent);
  pointer-events: none;
}
.sketch-selection__handle {
  fill: var(--surface, #fff);
  stroke: var(--accent);
  stroke-width: 1.5;
  pointer-events: none;
}
/* 抓取层盖住整张图；按下时自己判在不在把手附近，抓空了就是取消选中。 */
.sketch-selection__grab {
  position: absolute;
  inset: 0;
  pointer-events: auto;
  cursor: default;
}
</style>
