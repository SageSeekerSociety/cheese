<script setup lang="ts">
import type { Point, RasterRegion } from './designRegion'
import type { ShapeStroke, SketchStroke } from './designSketch'

import { computed } from 'vue'

import { arrowHeadPoints, fontSize, numberedStrokes } from './designSketch'

/** 屏幕上已画好的笔画。坐标一律原图像素，显示时乘 `scale`。 */
const props = defineProps<{ strokes: readonly SketchStroke[]; scale: number; naturalWidth: number }>()

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
</script>

<template>
  <svg class="sketch-overlay" aria-hidden="true">
    <template v-for="(item, position) in placed" :key="position">
      <polyline
        v-if="item.stroke.tool === 'pen'"
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
</style>
