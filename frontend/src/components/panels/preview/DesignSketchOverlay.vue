<script setup lang="ts">
import type { Point, RasterRegion } from './designRegion'
import type { ShapeStroke, SketchStroke } from './designSketch'

import { computed } from 'vue'

import { arrowHeadPoints, fontSize, numberedStrokes } from './designSketch'
import {
  drawsFrame,
  HANDLE_RADIUS,
  HANDLE_STROKE_WIDTH,
  SELECT_DASH,
  strokeBox,
  strokeHandles,
  TEXT_BOX_PADDING,
  TEXT_BOX_STROKE_WIDTH,
} from './designSketchSelection'

/** 屏幕上已画好的笔画。坐标一律原图像素，显示时乘 `scale`。 */
const props = defineProps<{
  strokes: readonly SketchStroke[]
  scale: number
  naturalWidth: number
  /** 选中的是哪一条（按索引）；画它的把手。 */
  selected?: number | null
  /** 正在编辑的那条文字（按索引）；编辑期间原文字不画，由输入框呈现。 */
  editing?: number | null
}>()

type Placed = {
  stroke: SketchStroke
  toDisplay: (point: Point) => Point
  box: (region: RasterRegion) => RasterRegion
}
const placed = computed<Placed[]>(() => {
  const scale = props.scale
  return props.strokes.map((stroke) => ({
    stroke,
    toDisplay: (point: Point) => ({ x: point.x * scale, y: point.y * scale }),
    box: (region: RasterRegion) => ({
      x: region.x * scale,
      y: region.y * scale,
      width: region.width * scale,
      height: region.height * scale,
    }),
  }))
})
const numbered = computed(() => numberedStrokes(props.strokes))
function badge(entry: { index: number; stroke: ShapeStroke }) {
  const x = entry.stroke.region.x * props.scale
  const y = entry.stroke.region.y * props.scale
  return {
    x,
    y,
    radius: Math.max(8, fontSize(props.naturalWidth) * 0.7) * props.scale,
    index: entry.index,
  }
}

/** 选中的那一笔（不在列表里、或正在编辑就是 null——编辑期间由输入框呈现）。 */
const selectedStroke = computed(() => {
  const index = props.selected
  if (index === null || index === undefined || index === props.editing) return null
  const stroke = props.strokes[index]
  return stroke && stroke.tool !== 'pen' ? stroke : null
})
/** 选中态：把手圆点（屏幕像素），以及文字才画的那圈虚线框。 */
const handles = computed(() =>
  selectedStroke.value ? strokeHandles(selectedStroke.value, props.scale, props.naturalWidth) : []
)
const frame = computed(() => {
  const stroke = selectedStroke.value
  if (!stroke || !drawsFrame(stroke)) return null
  const box = strokeBox(stroke, props.naturalWidth)
  return {
    x: (box.x - TEXT_BOX_PADDING) * props.scale,
    y: (box.y - TEXT_BOX_PADDING) * props.scale,
    width: (box.width + TEXT_BOX_PADDING * 2) * props.scale,
    height: (box.height + TEXT_BOX_PADDING * 2) * props.scale,
  }
})
const dash = SELECT_DASH.join(' ')
</script>

<template>
  <svg class="sketch-overlay" aria-hidden="true">
    <template v-for="(item, position) in placed" :key="position">
      <g v-if="editing === position" />
      <polyline
        v-else-if="item.stroke.tool === 'pen'"
        :points="item.stroke.points.map((point) => `${item.toDisplay(point).x},${item.toDisplay(point).y}`).join(' ')"
        fill="none"
        :stroke="item.stroke.color"
        :stroke-width="item.stroke.width * scale"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <g v-else-if="item.stroke.tool === 'line' || item.stroke.tool === 'arrow'">
        <line
          :x1="item.toDisplay(item.stroke.from).x"
          :y1="item.toDisplay(item.stroke.from).y"
          :x2="item.toDisplay(item.stroke.to).x"
          :y2="item.toDisplay(item.stroke.to).y"
          :stroke="item.stroke.color"
          :stroke-width="item.stroke.width * scale"
          stroke-linecap="round"
        />
        <polygon
          v-if="item.stroke.tool === 'arrow'"
          :points="
            arrowHeadPoints(item.stroke.from, item.stroke.to, item.stroke.width)
              .map((point) => `${item.toDisplay(point).x},${item.toDisplay(point).y}`)
              .join(' ')
          "
          :fill="item.stroke.color"
        />
      </g>
      <rect
        v-else-if="item.stroke.tool === 'rect' || item.stroke.tool === 'redact'"
        :x="item.box(item.stroke.region).x"
        :y="item.box(item.stroke.region).y"
        :width="item.box(item.stroke.region).width"
        :height="item.box(item.stroke.region).height"
        :fill="item.stroke.tool === 'redact' ? item.stroke.color : 'none'"
        :stroke="item.stroke.color"
        :stroke-width="item.stroke.width * scale"
      />
      <ellipse
        v-else-if="item.stroke.tool === 'ellipse'"
        :cx="item.box(item.stroke.region).x + item.box(item.stroke.region).width / 2"
        :cy="item.box(item.stroke.region).y + item.box(item.stroke.region).height / 2"
        :rx="item.box(item.stroke.region).width / 2"
        :ry="item.box(item.stroke.region).height / 2"
        fill="none"
        :stroke="item.stroke.color"
        :stroke-width="item.stroke.width * scale"
      />
      <text
        v-else-if="item.stroke.tool === 'text'"
        :x="item.toDisplay(item.stroke.at).x"
        :y="item.toDisplay(item.stroke.at).y"
        :fill="item.stroke.color"
        :font-size="fontSize(naturalWidth) * scale"
      >
        {{ item.stroke.text }}
      </text>
    </template>
    <g
      v-for="entry in numbered"
      :key="`badge-${entry.index}`"
      :transform="`translate(${badge(entry).x}, ${badge(entry).y})`"
    >
      <circle :r="badge(entry).radius" fill="#16181d" stroke="#ffffff" :stroke-width="badge(entry).radius * 0.16" />
      <text
        text-anchor="middle"
        dominant-baseline="central"
        fill="#ffffff"
        font-weight="600"
        :font-size="badge(entry).radius * 1.2"
      >
        {{ entry.index }}
      </text>
    </g>
    <!-- 选中态：只有文字画虚线框，其它形状只画把手圆点。 -->
    <rect
      v-if="frame"
      class="sketch-overlay__frame"
      :x="frame.x"
      :y="frame.y"
      :width="frame.width"
      :height="frame.height"
      fill="none"
      :stroke-width="TEXT_BOX_STROKE_WIDTH"
      :stroke-dasharray="dash"
    />
    <circle
      v-for="handle in handles"
      :key="`handle-${handle.role}`"
      class="sketch-overlay__handle"
      :cx="handle.x"
      :cy="handle.y"
      :r="HANDLE_RADIUS"
      :stroke-width="HANDLE_STROKE_WIDTH"
    />
  </svg>
</template>

<style scoped>
.sketch-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.sketch-overlay__frame {
  stroke: var(--accent);
}
.sketch-overlay__handle {
  fill: var(--accent);
  stroke: rgb(var(--v-theme-on-primary));
}
</style>
