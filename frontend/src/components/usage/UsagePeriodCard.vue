<script setup lang="ts">
import type { UsageLine, UsagePeriod, UsagePlan, UsageWindow } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import UsagePlanTag from '@/components/usage/UsagePlanTag.vue'
import { fmtPoints, fmtResetAt, LINE_KEY, linesOf, pct, remainingTone } from '@/lib/creditUsage'

// 方案这一块。按月发放的方案：本月用了多少点、还剩多少、什么时候重置，条按给的几条线
// 分色：个人页是协作、问答、写作、算力，团队页是协作和算力。按时间窗口限额的方案没有月额度，每个窗口一行：
// 用了多少、什么时候清零。方案名点开看方案包含什么。
const props = defineProps<{
  /** `null`：方案按时间窗口限额，看 `windows`。 */
  period: UsagePeriod | null
  plan: UsagePlan
  /** 「十月」这样的月份名。 */
  month: string
  /** 本月各条线用了多少点；给了就按线分色画条和图例。 */
  lines?: Partial<Record<UsageLine, number>> | null
  windows?: UsageWindow[]
  /** 方案之外的额度还剩多少点：方案额度用完后，从这里接着扣。 */
  otherCredits?: number
}>()

const { t, locale } = useI18n()

const used = computed(() => props.period?.credits_used ?? null)
const total = computed(() => props.period?.credits_total ?? null)
const remaining = computed(() => props.period?.remaining_ratio ?? null)
const usedUp = computed(() => remainingTone(remaining.value) === 'out')
/** 方案额度用完、还有其他额度时只是提醒：调用照常进行。 */
const tone = computed(() => (usedUp.value && (props.otherCredits ?? 0) > 0 ? 'low' : remainingTone(remaining.value)))
const usedUpText = computed(() =>
  (props.otherCredits ?? 0) > 0 ? t('usage.period.usedUpOther') : t('usage.period.usedUp')
)

function points(n: number): string {
  return fmtPoints(n, locale.value)
}

/** 这份用量分的几条线，图例和分色条都按它。 */
const shown = computed(() => linesOf(props.lines))

/** 分色条每一段的宽度：各条线用的点数占本月方案额度的比例。 */
const segments = computed(() => {
  const whole = total.value ?? 0
  const share = (n: number) => (whole > 0 ? Math.min(1, n / whole) : 0)
  if (!shown.value.length) return [{ line: 'collab' as UsageLine, width: share(used.value ?? 0) }]
  return shown.value.map((line) => ({ line, width: share(props.lines?.[line] ?? 0) }))
})

function windowUsed(w: UsageWindow): string {
  const value = pct(w.used_ratio)
  if (w.calendar === 'week') return t('usage.windows.week', { pct: value })
  if (w.calendar === 'month') return t('usage.windows.month', { pct: value })
  return t('usage.windows.used', { hours: w.hours, pct: value })
}
</script>

<template>
  <section class="upc" :aria-label="period ? t('usage.period.label') : t('usage.windows.title')">
    <template v-if="period">
      <div class="upc__head">
        <span class="upc__label">
          {{ t('usage.period.used', { month }) }}
          <UsagePlanTag :plan="plan" />
        </span>
        <span class="upc__figure t-num">
          {{
            period.unlimited || used === null || total === null
              ? t('usage.period.unlimited')
              : t('usage.period.figure', { used: points(used), total: points(total) })
          }}
        </span>
      </div>
      <template v-if="!period.unlimited && used !== null && total !== null">
        <div class="upc__meta">
          <span :class="`upc__left upc__left--${tone}`">
            {{ usedUp ? usedUpText : t('usage.period.remaining', { n: points(Math.max(0, total - used)) }) }}
          </span>
          <span v-if="period.resets_at">{{
            t('usage.period.resets', { at: fmtResetAt(period.resets_at, locale) })
          }}</span>
        </div>
        <div
          class="upc__bar"
          role="img"
          :aria-label="usedUp ? usedUpText : t('usage.period.usedAria', { used: points(used), total: points(total) })"
        >
          <span
            v-for="segment in segments"
            :key="segment.line"
            :class="`upc__seg upc__seg--${segment.line}`"
            :style="{ width: `${segment.width * 100}%` }"
          />
        </div>
      </template>
      <div v-if="lines && shown.length" class="upc__legend">
        <span v-for="line in shown" :key="line" class="upc__key">
          <span :class="`upc__dot upc__seg--${line}`" aria-hidden="true" />
          {{ t(LINE_KEY[line]) }}
          <span class="upc__keypct t-num">{{ t('usage.points', { n: points(lines[line] ?? 0) }) }}</span>
        </span>
      </div>
    </template>
    <template v-else>
      <div class="upc__head">
        <span class="upc__label">
          {{ t('usage.windows.title') }}
          <UsagePlanTag :plan="plan" />
        </span>
      </div>
      <div v-for="w in windows ?? []" :key="w.calendar ?? w.hours ?? ''" class="upc__window">
        <div class="upc__meta">
          <span :class="`upc__left upc__left--${remainingTone(1 - w.used_ratio)}`">{{ windowUsed(w) }}</span>
          <span v-if="w.resets_at">{{ t('usage.period.resets', { at: fmtResetAt(w.resets_at, locale) }) }}</span>
        </div>
        <div class="upc__bar" role="img" :aria-label="windowUsed(w)">
          <span class="upc__seg upc__seg--collab" :style="{ width: `${w.used_ratio * 100}%` }" />
        </div>
      </div>
    </template>
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
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.upc__label {
  display: inline-flex;
  white-space: nowrap;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.upc__figure {
  white-space: nowrap;
  color: var(--ink);
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
}

.upc__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.upc__window {
  display: flex;
  flex-direction: column;
  gap: 8px;
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

.upc__seg--compute {
  background: var(--usage-compute);
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
