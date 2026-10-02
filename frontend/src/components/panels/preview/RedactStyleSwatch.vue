<script setup lang="ts">
// 涂黑样式的小预览图（工具栏那一排）。参考物里样式小图和画布上的图案 tile 是**两套
// 独立实现**（小图是 3×3 / 7×7 的灰格 SVG，图案是真去画 96×96 的 tile）；把两者
// 合成一套会同时把两边都做坏——小图会糊、tile 会粗。这里就照小图那套画。
import type { RedactStyle } from './designSketch'

import { computed } from 'vue'

import { REDACT_INK } from './designSketch'

const props = withDefaults(defineProps<{ style: RedactStyle; size?: number }>(), { size: 16 })

/** 每种样式小图的网格：实心一颗黑块，马赛克 3×3，噪点 7×7。 */
const GRID: Record<RedactStyle, { cells: number; low: number; high: number }> = {
  solid: { cells: 0, low: 0, high: 0 },
  mosaic: { cells: 3, low: 56, high: 200 },
  noise: { cells: 7, low: 16, high: 240 },
}

/** 固定的灰格：用一个简单的线性同余式推出来，每次渲染长得一样，不抖。 */
function greys(cells: number, low: number, high: number): string[] {
  const out: string[] = []
  let seed = 7
  for (let index = 0; index < cells * cells; index += 1) {
    seed = (seed * 1103515245 + 12345) % 2147483648
    const value = low + Math.floor((seed / 2147483648) * (high - low + 1))
    out.push(`rgb(${value}, ${value}, ${value})`)
  }
  return out
}

const config = computed(() => GRID[props.style])
const palette = computed(() => greys(config.value.cells, config.value.low, config.value.high))
const cell = computed(() => props.size / Math.max(1, config.value.cells))
</script>

<template>
  <svg
    class="redact-swatch"
    :width="size"
    :height="size"
    :viewBox="`0 0 ${size} ${size}`"
    aria-hidden="true"
    focusable="false"
  >
    <rect v-if="style === 'solid'" :width="size" :height="size" :fill="REDACT_INK" />
    <template v-else>
      <rect
        v-for="(fill, index) in palette"
        :key="index"
        :x="(index % config.cells) * cell"
        :y="Math.floor(index / config.cells) * cell"
        :width="cell"
        :height="cell"
        :fill="fill"
      />
    </template>
  </svg>
</template>

<style scoped>
.redact-swatch {
  display: block;
  border: 1px solid var(--line-2);
}
</style>
