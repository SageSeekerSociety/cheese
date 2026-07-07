<!-- 飞书式「已阅」饼图：外面一个绿色圆环，里面一个绿色扇形，扇形的完整程度 = 群其他成员
     已读该消息的比例（0% → 只有空环，100% → 满绿实心圆）。tooltip 显示 已阅 X/Y。 -->
<template>
  <span class="read-pie" :title="`已阅 ${read}/${total}`" role="img" :aria-label="`已阅 ${read}/${total}`">
    <svg :width="size" :height="size" :viewBox="`0 0 ${D} ${D}`">
      <!-- 外环 -->
      <circle :cx="C" :cy="C" :r="ringR" fill="none" :stroke="green" :stroke-width="stroke" opacity="0.9" />
      <!-- 内饼：0<pct<1 用扇形 path，pct>=1 用整圆（避免 path 首尾重合不闭合） -->
      <circle v-if="pct >= 1" :cx="C" :cy="C" :r="pieR" :fill="green" />
      <path v-else-if="pct > 0" :d="slice" :fill="green" />
    </svg>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 已读的其他成员数 */
    read: number
    /** 其他成员总数（不含作者本人） */
    total: number
    size?: number
  }>(),
  { size: 16 },
)

const green = '#22c55e'
const D = 32 // viewBox 尺寸（内部坐标）
const C = D / 2
const stroke = 2.5
const ringR = C - stroke / 2 - 0.5
const pieR = ringR - stroke - 0.5

const pct = computed(() => {
  if (props.total <= 0) return 0
  return Math.max(0, Math.min(1, props.read / props.total))
})

// 从 12 点方向顺时针画一个占比 pct 的扇形。
const slice = computed(() => {
  const angle = pct.value * 2 * Math.PI
  const x = C + pieR * Math.sin(angle)
  const y = C - pieR * Math.cos(angle)
  const large = pct.value > 0.5 ? 1 : 0
  return `M ${C} ${C} L ${C} ${C - pieR} A ${pieR} ${pieR} 0 ${large} 1 ${x} ${y} Z`
})
</script>

<style scoped>
.read-pie {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 0;
  cursor: default;
}
</style>
