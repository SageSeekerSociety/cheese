<template>
  <p v-if="!data.analysed" class="an-note">{{ t('spaces.analytics.learning.class.behavior.empty') }}</p>
  <div v-else class="lc-page">
    <section class="an-card">
      <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.behavior.kindsTitle') }}</h3>
      <div class="lc-stack" role="img" :aria-label="kindsLabel">
        <span
          v-for="(kind, index) in data.kinds"
          :key="kind.kind"
          :style="{ width: `${kind.share * 100}%`, background: `var(--chart-${index + 1})` }"
        />
      </div>
      <div class="lc-legend">
        <span v-for="(kind, index) in data.kinds" :key="kind.kind" class="lc-muted">
          <span class="lc-swatch" :style="{ background: `var(--chart-${index + 1})` }" />
          {{
            t('spaces.analytics.learning.class.behavior.kindShare', {
              kind: t(`spaces.analytics.learning.class.behavior.kinds.${kind.kind}`),
              pct: `${Math.round(kind.share * 100)}%`,
            })
          }}
        </span>
      </div>
    </section>
    <div class="an-grid">
      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.behavior.heatTitle') }}</h3>
        <table class="lc-heat">
          <thead>
            <tr>
              <th />
              <th v-for="slot in data.slots" :key="slot">{{ slot }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, day) in data.heat" :key="day">
              <th>{{ t(`spaces.analytics.learning.class.behavior.days.${day + 1}`) }}</th>
              <td v-for="(value, slot) in row" :key="slot">
                <span
                  class="lc-heat__cell"
                  :style="{ opacity: value == null ? 0.08 : 0.15 + (value / most) * 0.85 }"
                  :title="
                    value == null ? t('spaces.analytics.learning.class.notAnalysed') : cellLabel(day, slot, value)
                  "
                />
              </td>
            </tr>
          </tbody>
        </table>
        <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.behavior.heatHint') }}</p>
      </section>
      <section v-if="data.dependency" class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.behavior.depTitle') }}</h3>
        <p class="an-note">{{ t('spaces.analytics.learning.class.behavior.depHint') }}</p>
        <div v-for="tier in TIERS" :key="tier" class="lc-tier">
          <span class="lc-muted">{{ t(`spaces.analytics.learning.class.behavior.dep.${tier}`) }}</span>
          <div class="lc-tier__bar">
            <span :class="`lc-tier__${tier}`" :style="{ width: `${(data.dependency[tier] / depTotal) * 100}%` }" />
          </div>
          <span class="lc-muted">{{
            t('spaces.analytics.learning.class.behavior.people', { n: data.dependency[tier] })
          }}</span>
        </div>
        <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.behavior.depNote') }}</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// 提问行为：提问类型占比、星期 × 时段热力（不足 3 人的格子留白）、依赖程度三档人数。
import type { LearningClassBehavior } from '@/types/apiSpaces'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ data: LearningClassBehavior }>()
const { t } = useI18n()

const TIERS = ['low', 'mid', 'high'] as const

const most = computed(() => Math.max(1, ...props.data.heat.flat().map((value) => value ?? 0)))
const depTotal = computed(() => {
  const dependency = props.data.dependency
  return dependency ? Math.max(1, dependency.low + dependency.mid + dependency.high) : 1
})
const kindsLabel = computed(() =>
  props.data.kinds
    .map(
      (kind) => `${t(`spaces.analytics.learning.class.behavior.kinds.${kind.kind}`)} ${Math.round(kind.share * 100)}%`
    )
    .join(' · ')
)
const cellLabel = (day: number, slot: number, value: number) =>
  t('spaces.analytics.learning.class.behavior.heatCell', {
    day: t(`spaces.analytics.learning.class.behavior.days.${day + 1}`),
    slot: props.data.slots[slot],
    n: value,
  })
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-stack {
  display: flex;
  height: 24px;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--fill);
}

.lc-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 10px;
}

.lc-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 4px;
  border-radius: var(--radius-sm);
  vertical-align: -1px;
}

.lc-heat {
  border-collapse: separate;
  border-spacing: 4px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}

.lc-heat th {
  font-weight: 400;
  text-align: center;
}

.lc-heat__cell {
  display: block;
  width: 44px;
  height: 22px;
  border-radius: var(--radius-sm);
  background: var(--accent);
}

.lc-tier {
  display: grid;
  grid-template-columns: 100px 1fr 60px;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}

.lc-tier__bar {
  height: 10px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--fill);
}

.lc-tier__bar span {
  display: block;
  height: 100%;
}

.lc-tier__low {
  background: var(--ok);
}

.lc-tier__mid {
  background: var(--warn);
}

.lc-tier__high {
  background: var(--danger);
}
</style>
