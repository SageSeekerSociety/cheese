<script setup lang="ts">
import type { Point, RasterRegion } from './designRegion'
import type { ShapeStroke, SketchStroke } from './designSketch'

import { computed, useId } from 'vue'

import {
  arrowHeadPoints,
  fontSize,
  numberedStrokes,
  REDACT_INK,
  REDACT_TILE,
  redactAnchor,
  redactTileDataUrl,
} from './designSketch'
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

/** 这个实例的 pattern id 前缀：同一页挂两块覆盖层时，id 不能撞。
 *  计数变量写在 `<script setup>` 里每建一个实例就归零，起不到区分作用——用 useId。 */
const uid = `redact-${useId()}`

/**
 * 涂黑图案的贴图。一条非实心的涂黑一个 pattern——相位按它自己的左上角取，所以
 * 相邻的两块涂黑不会拼成一片连续的纹理（导出那边也是这么做的）。
 *
 * 和导出用同一块 tile（`redactTileDataUrl` 里缓存的），屏幕上是那块纹的样子，
 * 不是三种样式都画成纯黑。没有画布的宿主给不出 tile，就退回纯黑。
 */
const redactMasks = computed(() => {
  const scale = props.scale
  const tile = REDACT_TILE * scale
  if (!(tile > 0)) return []
  const masks: { id: string; url: string; size: number; x: number; y: number; at: number }[] = []
  placed.value.forEach((item, position) => {
    const stroke = item.stroke
    if (stroke.tool !== 'redact') return
    const url = redactTileDataUrl(stroke.redactStyle ?? 'solid')
    if (!url) return
    const anchor = redactAnchor(stroke.region.x, stroke.region.y)
    masks.push({
      id: `${uid}-${position}`,
      url,
      size: tile,
      x: anchor.x * scale,
      y: anchor.y * scale,
      at: position,
    })
  })
  return masks
})

/** 这一条涂黑该用哪块贴图；没有就纯黑。 */
function redactFill(position: number) {
  const mask = redactMasks.value.find((entry) => entry.at === position)
  return mask ? `url(#${mask.id})` : REDACT_INK
}
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
    <defs>
      <pattern
        v-for="mask in redactMasks"
        :id="mask.id"
        :key="mask.id"
        patternUnits="userSpaceOnUse"
        :x="mask.x"
        :y="mask.y"
        :width="mask.size"
        :height="mask.size"
      >
        <image :href="mask.url" :width="mask.size" :height="mask.size" />
      </pattern>
    </defs>
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
        :fill="item.stroke.tool === 'redact' ? redactFill(position) : 'none'"
        :stroke="item.stroke.tool === 'redact' ? REDACT_INK : item.stroke.color"
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
      <!-- `at` 处处当左上角用：命中盒、选中虚线框、输入框、导出画布（textBaseline
           = 'top'）都是这样。只有这里原来按默认基线画，字会掉到框下面一个字号，
           于是「点在字上不中、点在字下面的空白却中」。 -->
      <text
        v-else-if="item.stroke.tool === 'text'"
        :x="item.toDisplay(item.stroke.at).x"
        :y="item.toDisplay(item.stroke.at).y"
        dominant-baseline="text-before-edge"
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
