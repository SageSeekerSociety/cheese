<script setup lang="ts">
import type { UsageDay, UsageLine } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtMonthDay, fmtPoints, linesOf } from '@/lib/creditUsage'

// 每天用量：一天一根柱子，高度按本月用得最多的那天折算。分了线就按线分色叠起来。
const props = defineProps<{
  days: UsageDay[]
  /** 按线叠色：个人页四条线，团队页协作和算力。 */
  stacked?: boolean
}>()

const { t, locale } = useI18n()

const peak = computed(() => Math.max(0, ...props.days.map((d) => d.credits ?? 0)))

function height(value: number): string {
  return peak.value > 0 ? `${(value / peak.value) * 100}%` : '0%'
}

function parts(day: UsageDay): { line: UsageLine | 'all'; value: number }[] {
  if (props.stacked && day.lines) return linesOf(day.lines).map((line) => ({ line, value: day.lines?.[line] ?? 0 }))
  return [{ line: 'all', value: day.credits ?? 0 }]
}

function label(day: UsageDay): string {
  return t('usage.daily.aria', {
    date: fmtMonthDay(day.date, locale.value),
    n: fmtPoints(day.credits ?? 0, locale.value),
  })
}

/** 轴上只标月初、月中、月末三天。 */
const ticks = computed(() => {
  const n = props.days.length
  if (!n) return []
  return [props.days[0], props.days[Math.min(14, n - 1)], props.days[n - 1]].map((d) =>
    d ? `${Number(d.date.slice(5, 7))}/${Number(d.date.slice(8, 10))}` : ''
  )
})
</script>

<template>
  <section class="udc" :aria-label="t('usage.daily.title')">
    <h2 class="udc__title t-title">{{ t('usage.daily.title') }}</h2>
    <div class="udc__plot">
      <div
        v-for="day in days"
        :key="day.date"
        class="udc__col"
        :class="{ 'udc__col--future': day.credits === null, 'udc__col--idle': day.credits === 0 }"
        :title="day.credits === null ? undefined : label(day)"
      >
        <template v-if="day.credits">
          <span
            v-for="part in parts(day)"
            :key="part.line"
            :class="`udc__part udc__part--${part.line}`"
            :style="{ height: height(part.value) }"
          />
        </template>
      </div>
    </div>
    <div class="udc__axis" aria-hidden="true">
      <span v-for="(tick, i) in ticks" :key="i">{{ tick }}</span>
    </div>
  </section>
</template>

<style scoped>
.udc {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.udc__title {
  margin: 0;
  color: var(--ink);
}

.udc__plot {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 120px;
  border-bottom: 1px solid var(--line);
}

.udc__col {
  display: flex;
  flex: 1 1 0;
  flex-direction: column-reverse;
  height: 100%;
  min-width: 0;
}

.udc__col--idle::after {
  content: '';
  display: block;
  height: 2px;
  background: var(--line-2);
}

.udc__part {
  display: block;
  flex: none;
}

.udc__part--all,
.udc__part--collab {
  background: var(--usage-collab);
}

.udc__part--ask {
  background: var(--usage-ask);
}

.udc__part--write {
  background: var(--usage-write);
}

.udc__part--compute {
  background: var(--usage-compute);
}

.udc__axis {
  display: flex;
  justify-content: space-between;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
