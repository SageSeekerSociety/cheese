<script setup lang="ts">
import { computed } from 'vue'

// 「部分对整体」的一根堆叠条：打回漏斗、有用率 👍/👎、OAuth 三档都用它。
//
// **段间留 2px 底色缝**（dataviz 的 spacer 规则）：不留缝的两段同色会糊成一根。
// **不用双色对立配色**（红/绿）—— 项目 §2.7 的裁决是中性明度 + 线型；所以三段是
// `ink / muted / faint` 三级明度，图例在下面直接写着每段是什么。
//
// 段本身是状态语义时（比如「已耗尽」）才允许 tone，而且**必须**带文字图例 —— 不能
// 只靠颜色说话。
const props = withDefaults(
  defineProps<{
    title?: string
    segments: { label: string; value: number; shade: 'ink' | 'muted' | 'faint' }[]
    /** 口径注。**必填**：这根条上的比例不写清楚会被读成转化率。 */
    note: string
    loading?: boolean
  }>(),
  { loading: false, title: undefined },
)

const total = computed(() => props.segments.reduce((a, s) => a + s.value, 0))
const hasData = computed(() => total.value > 0)

function widthOf(value: number): string {
  return `${(value / Math.max(1, total.value)) * 100}%`
}
</script>

<template>
  <div class="ash">
    <div v-if="title" class="ash__head">
      <span class="ash__title t-eyebrow-read">{{ title }}</span>
    </div>

    <div v-if="loading" class="ash__skel">
      <v-skeleton-loader type="text" class="ash__skel-bar" />
    </div>

    <template v-else>
      <!-- 空态给破折号语义：没有数据 ≠ 全是 0。 -->
      <p v-if="!hasData" class="ash__none t-meta-read">—</p>

      <div v-else class="ash__bar" aria-hidden="true">
        <span
          v-for="(seg, i) in segments"
          :key="seg.label"
          class="ash__seg"
          :class="`ash__seg--${seg.shade}`"
          :style="{ width: widthOf(seg.value) }"
          :data-first="i === 0"
          :data-last="i === segments.length - 1"
        />
      </div>

      <!-- 图例是**真文本**（不是 aria-hidden 的色块），读屏直接读得到。 -->
      <ol class="ash__legend">
        <li v-for="seg in segments" :key="seg.label" class="ash__leg">
          <span class="ash__swatch" :class="`ash__seg--${seg.shade}`" aria-hidden="true" />
          <span class="ash__leg-label t-meta-read">{{ seg.label }}</span>
          <span class="ash__leg-value t-num t-dense">{{ seg.value }}</span>
        </li>
      </ol>

      <p class="ash__note t-meta-read">{{ note }}</p>
    </template>
  </div>
</template>

<style scoped>
.ash {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.ash__bar {
  display: flex;
  height: 14px;
  gap: 2px;
  /* 2px 缝用卡片底色把段分开 —— 不留缝时两段会糊在一起。 */
  background: var(--surface);
  border-radius: 2px;
  overflow: hidden;
}

.ash__seg {
  display: block;
  height: 100%;
  min-width: 2px;
}

/* 三级明度，不是双色对立。 */
.ash__seg--ink {
  background: var(--ink);
}
.ash__seg--muted {
  background: var(--muted);
}
.ash__seg--faint {
  background: var(--line-2);
}

.ash__seg[data-first='true'] {
  border-radius: 4px 2px 2px 4px;
}
.ash__seg[data-last='true'] {
  border-radius: 2px 4px 4px 2px;
}

.ash__legend {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ash__leg {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.ash__swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.ash__leg-value {
  color: var(--ink);
}

.ash__note {
  margin: 0;
}

.ash__none {
  margin: 0;
  font-size: 18px;
}

.ash__skel-bar {
  height: 14px;
}
</style>
