<script setup lang="ts">
import type { UsageLine, UsagePeriod, UsageWindow } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtResetAt, LINE_KEY, pct, remainingTone, USAGE_LINES } from '@/lib/creditUsage'

// 本月这一块：方案、用了多少、还剩多少、什么时候重置。个人页的条按产品线分三色，
// 团队页是一种颜色。方案有使用上限时，每条上限一行。
const props = defineProps<{
  period: UsagePeriod
  planName: string
  /** 「十月」这样的月份名。 */
  month: string
  /** 本月用量按产品线分的比例；给了就画三色条和图例。 */
  lines?: Record<UsageLine, number> | null
  windows?: UsageWindow[]
}>()

const { t, locale } = useI18n()

const used = computed(() => props.period.used_ratio)
const remaining = computed(() => props.period.remaining_ratio)
const tone = computed(() => remainingTone(remaining.value))
const usedUp = computed(() => tone.value === 'out')

/** 三色条每一段的宽度，也是图例上的数：已用的比例按各产品线的份额分，三段加起来就是已用。 */
const segments = computed(() => {
  const u = used.value ?? 0
  if (!props.lines) return [{ line: 'collab' as UsageLine, width: u }]
  return USAGE_LINES.map((line) => ({ line, width: u * (props.lines?.[line] ?? 0) }))
})
</script>

<template>
  <section class="upc" :aria-label="t('usage.period.label')">
    <div class="upc__head">
      <span class="upc__label">
        {{ t('usage.period.used', { month }) }}
        <span class="upc__plan">{{ planName }}</span>
      </span>
      <span class="upc__figure t-num">
        {{ period.unlimited || used === null ? t('usage.period.unlimited') : pct(used) }}
      </span>
    </div>

    <template v-if="!period.unlimited && used !== null">
      <div class="upc__meta">
        <span :class="`upc__left upc__left--${tone}`">
          {{ usedUp ? t('usage.period.usedUp') : t('usage.period.remaining', { pct: pct(remaining ?? 0) }) }}
        </span>
        <span v-if="period.resets_at">{{
          t('usage.period.resets', { at: fmtResetAt(period.resets_at, locale) })
        }}</span>
      </div>
      <div
        class="upc__bar"
        role="img"
        :aria-label="usedUp ? t('usage.period.usedUp') : t('usage.period.usedAria', { pct: pct(used) })"
      >
        <span
          v-for="segment in segments"
          :key="segment.line"
          :class="`upc__seg upc__seg--${segment.line}`"
          :style="{ width: `${segment.width * 100}%` }"
        />
      </div>
    </template>

    <div v-if="lines" class="upc__legend">
      <span v-for="line in USAGE_LINES" :key="line" class="upc__key">
        <span :class="`upc__dot upc__seg--${line}`" aria-hidden="true" />
        {{ t(LINE_KEY[line]) }}
        <span class="upc__keypct t-num">{{ pct((used ?? 0) * (lines[line] ?? 0)) }}</span>
      </span>
    </div>

    <div v-for="w in windows ?? []" :key="w.hours" class="upc__window">
      <span>{{ t('usage.windows.used', { hours: w.hours, pct: pct(w.used_ratio) }) }}</span>
      <span v-if="w.reopens_at" class="upc__left upc__left--out">{{
        t('usage.windows.reopens', { at: fmtResetAt(w.reopens_at, locale) })
      }}</span>
    </div>
  </section>
</template>

<style scoped>
.upc {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upc__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.upc__label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.upc__plan {
  padding: 0 6px;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
}

.upc__figure {
  color: var(--ink);
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
}

.upc__meta,
.upc__window {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.upc__left--low {
  color: var(--warn-ink);
  font-weight: 600;
}

.upc__left--out {
  color: var(--danger-ink);
  font-weight: 600;
}

.upc__bar {
  display: flex;
  height: 10px;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.upc__seg {
  display: block;
  height: 100%;
}

.upc__seg--collab {
  background: var(--usage-collab);
}

.upc__seg--ask {
  background: var(--usage-ask);
}

.upc__seg--write {
  background: var(--usage-write);
}

.upc__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.upc__key {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.upc__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}

.upc__keypct {
  color: var(--muted);
}
</style>
