<script setup lang="ts">
import { computed } from 'vue'

// 迷你折线（sparkline）：KPI 卡第三行、表格行内、网络格共用的「形状层」读数。
//
// 它不是图表——不画点、不画轴、无交互，精确的逐点值由相邻的文字或该块自己的
// <details> 数据表承担（§2.7 的可访问形式在那些地方，不在这根线上）。所以它
// aria-hidden，纯粹承担「趋势长什么样」的一眼形状。
//
// `values` 里的 null 表示缺数：断段而不是连过去——缺数和是零是两回事。
const props = withDefaults(
  defineProps<{
    /** 2–90 个点；null 断段。全 null 或不足 2 个有效点时渲染空。 */
    values: (number | null)[]
    /** 像素高，默认 20。 */
    height?: number
  }>(),
  { height: 20 }
)

const PAD = 2

const path = computed(() => {
  const pts = props.values
  const nums = pts.filter((v): v is number => v !== null)
  if (nums.length < 2) return ''
  const min = Math.min(...nums)
  const span = Math.max(...nums) - min
  const innerH = props.height - PAD * 2
  const x = (i: number) => (pts.length < 2 ? 0 : (i / (pts.length - 1)) * 100)
  const y = (v: number) =>
    PAD + (span === 0 ? innerH / 2 : innerH - ((v - min) / span) * innerH)
  let d = ''
  let pen = false
  for (let i = 0; i < pts.length; i++) {
    const v = pts[i]
    if (v === null) {
      pen = false
      continue
    }
    d += `${pen ? ' L' : 'M'}${x(i).toFixed(2)} ${y(v).toFixed(2)}`
    pen = true
  }
  return d
})
</script>

<template>
  <svg
    class="aspark"
    :viewBox="`0 0 100 ${height}`"
    :height="height"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <path v-if="path" :d="path" vector-effect="non-scaling-stroke" />
  </svg>
</template>

<style scoped>
.aspark {
  display: block;
  width: 100%;
  min-width: 0;
}

.aspark path {
  fill: none;
  stroke: var(--muted);
  stroke-width: 1.5px;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
