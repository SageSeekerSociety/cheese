<script setup lang="ts">
import type { RatchetPoint } from '@/views/admin/ratchetApi'

import { computed } from 'vue'

// 一道检查的走势小图，画在表里那一行上。
//
// 三件事决定它不是一条普通折线：
//
// 1. **`actual` 是 null 的点不是 0。** 洞（这次没跑到）会把线断开，而不是让线掉到
//    底下——掉下去看起来像「债还完了」，正好是把「没量到」读成「变好了」。
// 2. **规则指纹变了的点单独标出来。** 那是新的比较起点，跨过它的两点不该被连成一段。
// 3. **点数少于 2 就不画。** 一个点没有走势；画一条平线会让人以为「一直都没动」。
//
// 图上一律不给刻度：它是「方向」而不是「读数」——读数在同行那一列里，同一个数不写两遍。
defineOptions({ name: 'AdminRatchetSparkline' })

const props = defineProps<{
  points: RatchetPoint[]
  /** 服务端算好的方向，用来定这条线的语气（好的绿、坏的红、平/未知的中性灰）。 */
  direction: 'improving' | 'worse' | 'flat' | 'unknown'
}>()

const W = 96
const H = 22
const PAD = 3

/** 有数的点：位置按它在序列里的顺序给（不是按时间，时间未必等距，但格数才是读得出来的）。 */
const measured = computed(() =>
  props.points
    .map((point, index) => ({ index, value: point.actual }))
    .filter((item): item is { index: number; value: number } => item.value !== null)
)

const values = computed(() => measured.value.map((item) => item.value))

const x = (index: number) => {
  const last = Math.max(props.points.length - 1, 1)
  return PAD + (index / last) * (W - PAD * 2)
}

const y = (value: number) => {
  const list = values.value
  const min = Math.min(...list)
  const max = Math.max(...list)
  // 一条水平线（所有点一样）画在中间，不画在最底下。
  if (max === min) return H / 2
  return H - PAD - ((value - min) / (max - min)) * (H - PAD * 2)
}

/** 连续的点连成一条折线；洞和规则变更都把线切断。 */
const segments = computed(() => {
  const list = measured.value
  const out: { index: number; value: number }[][] = []
  let current: { index: number; value: number }[] = []
  for (const item of list) {
    const point = props.points[item.index]
    const previous = current[current.length - 1]
    const gap = previous === undefined ? false : point.rule_changed || item.index !== previous.index + 1
    if (gap) {
      if (current.length > 1) out.push(current)
      current = []
    }
    current.push(item)
  }
  if (current.length > 1) out.push(current)
  return out
})

const dots = computed(() => {
  const singles = measured.value.filter((item) => !segments.value.some((segment) => segment.includes(item)))
  return singles
})

const line = (segment: { index: number; value: number }[]) =>
  segment.map((item) => `${x(item.index).toFixed(1)},${y(item.value).toFixed(1)}`).join(' ')
</script>

<template>
  <svg
    v-if="measured.length >= 2"
    class="ars"
    :class="`ars--${direction}`"
    :width="W"
    :height="H"
    :viewBox="`0 0 ${W} ${H}`"
    role="img"
    aria-hidden="true"
  >
    <polyline v-for="(segment, i) in segments" :key="i" class="ars__line" :points="line(segment)" />
    <circle v-for="item in dots" :key="item.index" class="ars__dot" :cx="x(item.index)" :cy="y(item.value)" r="1.6" />
    <!-- 最后一个点单独点出来：表里那一列的数就是它。 -->
    <circle
      class="ars__end"
      :cx="x(measured[measured.length - 1].index)"
      :cy="y(measured[measured.length - 1].value)"
      r="2.2"
    />
  </svg>
</template>

<style scoped>
.ars {
  display: block;
  overflow: visible;
}

.ars__line {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  stroke-linejoin: round;
  stroke-linecap: round;
  opacity: 0.55;
}

.ars__dot,
.ars__end {
  fill: currentColor;
}

.ars__end {
  opacity: 0.9;
}

.ars--improving {
  color: var(--ok-ink);
}

.ars--worse {
  color: var(--danger-ink);
}

.ars--flat,
.ars--unknown {
  color: var(--muted);
}
</style>
