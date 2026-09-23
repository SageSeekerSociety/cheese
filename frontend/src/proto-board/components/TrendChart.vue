<script setup lang="ts">
// 走势图。仓库里没有图表库（package.json 搜 chart/echarts/apex/d3 零命中），
// 现有分析页也是手写 SVG —— 这里照同一条路数走，只是把「一条线 + 填充」扩成
// 可叠多条线，因为看板要把「领取」和「提交」放在同一张图上比。
//
// 交互只有一种：鼠标移入时在最近的时间点上打一条竖线并列出各条线的值。
// 不做缩放、不做刷选 —— 原型要说明的是「这里该有张什么图」，不是把图表库抄一遍。
import { computed, ref } from 'vue'

const props = defineProps<{
  /** 横轴标签，长度决定点的个数。 */
  labels: string[]
  series: { name: string; values: number[]; tone?: 'primary' | 'ok' | 'muted' }[]
  height?: number
}>()

const W = 640
const H = 200
const PAD = { top: 16, right: 12, bottom: 26, left: 34 }

const innerW = W - PAD.left - PAD.right
const innerH = H - PAD.top - PAD.bottom

const maxValue = computed(() => {
  const all = props.series.flatMap((s) => s.values)
  return Math.max(1, ...all)
})

/** 「好看的上界」：把最大值抬到一个整齐的刻度上，横线才不会是 37 这种数。 */
const niceMax = computed(() => {
  const raw = maxValue.value
  const step = raw <= 10 ? 2 : raw <= 40 ? 10 : raw <= 100 ? 20 : 50
  return Math.ceil(raw / step) * step
})

const ticks = computed(() => {
  const n = 4
  return Array.from({ length: n + 1 }, (_, i) => Math.round((niceMax.value / n) * i))
})

function xAt(i: number): number {
  const n = props.labels.length
  if (n <= 1) return PAD.left + innerW / 2
  return PAD.left + (i / (n - 1)) * innerW
}

function yAt(v: number): number {
  return PAD.top + innerH - (v / niceMax.value) * innerH
}

function pointsOf(values: number[]): string {
  return values.map((v, i) => `${xAt(i)},${yAt(v)}`).join(' ')
}

function areaOf(values: number[]): string {
  if (!values.length) return ''
  return `${xAt(0)},${PAD.top + innerH} ${pointsOf(values)} ${xAt(values.length - 1)},${PAD.top + innerH}`
}

const hoverIndex = ref<number | null>(null)

function onMove(event: MouseEvent) {
  const target = event.currentTarget as SVGSVGElement
  const box = target.getBoundingClientRect()
  const rel = ((event.clientX - box.left) / box.width) * W
  const n = props.labels.length
  if (n <= 1) {
    hoverIndex.value = 0
    return
  }
  const ratio = (rel - PAD.left) / innerW
  hoverIndex.value = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))))
}

const STROKE: Record<string, string> = {
  primary: 'rgba(25,118,210,0.95)',
  ok: 'rgba(31,157,85,0.95)',
  muted: 'rgba(120,126,134,0.85)',
}
</script>

<template>
  <div class="trend" :style="{ height: `${height ?? H}px` }">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" @mousemove="onMove" @mouseleave="hoverIndex = null">
      <!-- 网格：四条横线加刻度值。虚线、极淡，只在读值时提供参照，不抢线的注意力。 -->
      <g>
        <template v-for="t in ticks" :key="t">
          <line
            :x1="PAD.left"
            :x2="W - PAD.right"
            :y1="yAt(t)"
            :y2="yAt(t)"
            stroke="rgba(120,126,134,0.18)"
            stroke-width="1"
            stroke-dasharray="3 4"
          />
          <text :x="PAD.left - 8" :y="yAt(t) + 4" text-anchor="end" class="trend__tick">{{ t }}</text>
        </template>
      </g>

      <!-- 第一条线带填充，其余只有描边：两条都有填充会互相盖住。 -->
      <polyline v-if="series[0]" :points="areaOf(series[0].values)" fill="rgba(25,118,210,0.08)" stroke="none" />
      <polyline
        v-for="(s, si) in series"
        :key="s.name"
        :points="pointsOf(s.values)"
        fill="none"
        :stroke="STROKE[s.tone ?? (si === 0 ? 'primary' : 'ok')]"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />

      <!-- 悬停：一条竖线 + 每个系列一个点，值列在图下方的图例里。 -->
      <template v-if="hoverIndex !== null">
        <line
          :x1="xAt(hoverIndex)"
          :x2="xAt(hoverIndex)"
          :y1="PAD.top"
          :y2="PAD.top + innerH"
          stroke="rgba(120,126,134,0.45)"
          stroke-width="1"
        />
        <circle
          v-for="(s, si) in series"
          :key="s.name"
          :cx="xAt(hoverIndex)"
          :cy="yAt(s.values[hoverIndex] ?? 0)"
          r="3.5"
          :fill="STROKE[s.tone ?? (si === 0 ? 'primary' : 'ok')]"
        />
      </template>

      <!-- 横轴只标首、中、尾：12 个日期全铺上会糊成一条。 -->
      <template v-for="i in labels.length" :key="`x${i}`">
        <text
          v-if="i === 1 || i === Math.ceil(labels.length / 2) || i === labels.length"
          :x="xAt(i - 1)"
          :y="H - 8"
          text-anchor="middle"
          class="trend__tick"
        >
          {{ labels[i - 1] }}
        </text>
      </template>
    </svg>

    <div class="trend__legend">
      <span v-for="(s, si) in series" :key="s.name" class="trend__legend-item">
        <i :style="{ background: STROKE[s.tone ?? (si === 0 ? 'primary' : 'ok')] }" />
        {{ s.name }}
        <b v-if="hoverIndex !== null">{{ s.values[hoverIndex] ?? 0 }}</b>
        <b v-else>{{ s.values.at(-1) ?? 0 }}</b>
      </span>
      <span v-if="hoverIndex !== null" class="trend__legend-day">{{ labels[hoverIndex] }}</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.trend {
  display: flex;
  flex-direction: column;
}

.trend svg {
  flex: 1;
  width: 100%;
  min-height: 0;
}

.trend__tick {
  fill: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 10px;
}

.trend__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  margin-top: 6px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.78rem;
}

.trend__legend-item {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.trend__legend-item i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.trend__legend-item b {
  color: rgba(var(--v-theme-on-surface), 0.9);
  font-weight: 600;
}

.trend__legend-day {
  margin-left: auto;
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
