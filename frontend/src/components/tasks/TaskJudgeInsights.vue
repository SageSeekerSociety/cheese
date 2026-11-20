<script setup lang="ts">
// 「数据」页签里测试点那一块：每个用例过了几份（最差的在前）、测试分分布、评测累计花的
// 额度。读的都是每个参与者在这一阶段最新那一版提交的最近一次评测（后端 `judge_stats`）；
// 取数在 `views/tasks/detail/Insights.vue`，这里只画。
import type { TaskJudgeCaseRate, TaskJudgeStatsView } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import MetricCard from '@/components/spaces/MetricCard.vue'
import PanelCard from '@/components/spaces/PanelCard.vue'

const props = defineProps<{
  data: TaskJudgeStatsView
  /** 阶段 id → 阶段名；不分阶段的题是空的。 */
  stageNames: Record<string, string>
}>()

const emit = defineEmits<{ stage: [stage: string | null] }>()

const { t, te } = useI18n()

const stats = computed(() => props.data.stats)
const pct = (rate: number | null | undefined) => (rate == null ? '—' : `${Math.round(rate * 100)}%`)

function statusLabel(status: string): string {
  const key = `tasks.judge.status.${status}`
  return te(key) ? t(key) : t('tasks.judge.status.unknown')
}

function failureLine(row: TaskJudgeCaseRate): string {
  if (!row.topFailure) return t('tasks.judgeInsights.allPassed')
  return t('tasks.judgeInsights.topFailure', {
    n: row.topFailure.count,
    status: statusLabel(row.topFailure.status),
  })
}

/** 十档的标签：0–9%、10–19% …… 90–100%。 */
const bucketLabels = Array.from({ length: 10 }, (_, i) => (i === 9 ? '90–100%' : `${i * 10}–${i * 10 + 9}%`))
const bucketMax = computed(() => Math.max(1, ...(stats.value?.buckets ?? [])))

const credits = computed(() => props.data.credits)
const fmtCredits = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2))
const machineMinutes = computed(() => Math.ceil(credits.value.machineMs / 60_000))

function stageLabel(stage: string | null, index: number): string {
  return (stage && props.stageNames[stage]) || t('tasks.judgeInsights.stageN', { n: index + 1 })
}
</script>

<template>
  <PanelCard
    class="tji"
    :title="t('tasks.judgeInsights.title')"
    :subtitle="t('tasks.judgeInsights.subtitle')"
    data-testid="insights-judge"
  >
    <template v-if="data.stages.length > 1" #actions>
      <v-btn-toggle
        :model-value="data.stage"
        density="compact"
        mandatory
        variant="outlined"
        divided
        @update:model-value="(value: string | null) => emit('stage', value)"
      >
        <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
        <v-btn v-for="(stage, i) in data.stages" :key="stage ?? i" :value="stage" size="small">
          {{ stageLabel(stage, i) }}
        </v-btn>
      </v-btn-toggle>
    </template>

    <div class="tji__kpis">
      <MetricCard
        :label="t('tasks.judgeInsights.average')"
        :value="pct(stats?.averageRate)"
        icon="mdi-check-decagram-outline"
        :hint="t('tasks.judgeInsights.averageHint', { judged: stats?.judged ?? 0, total: stats?.submissions ?? 0 })"
      />
      <MetricCard
        :label="t('tasks.judgeInsights.credits')"
        :value="fmtCredits(credits.total)"
        icon="mdi-lightning-bolt-outline"
        :hint="
          t('tasks.judgeInsights.creditsHint', {
            submissions: fmtCredits(credits.submissions),
            selfTests: fmtCredits(credits.selfTests),
            minutes: machineMinutes,
          })
        "
      />
    </div>

    <template v-if="stats">
      <v-alert v-if="stats.outdated" type="info" variant="tonal" density="compact" class="tji__gap">
        {{ t('tasks.judgeInsights.outdated', { n: stats.outdated }) }}
      </v-alert>

      <div class="tji__grid">
        <section>
          <h4 class="tji__h">{{ t('tasks.judgeInsights.casesTitle') }}</h4>
          <ul v-if="stats.cases.length" class="tji__cases" data-testid="insights-judge-cases">
            <li v-for="row in stats.cases" :key="row.name" class="tji__case">
              <div class="tji__case-top">
                <span class="tji__name" :title="row.name">{{ row.name }}</span>
                <span v-if="row.visibility === 'HIDDEN'" class="tji__tag">{{ t('tasks.judgeInsights.hidden') }}</span>
                <span class="tji__count">
                  {{ t('tasks.judgeInsights.passedOf', { passed: row.passed, judged: row.judged }) }}
                  · {{ pct(row.rate) }}
                </span>
              </div>
              <span class="tji__track">
                <i
                  :class="{ 'tji__bar--low': (row.rate ?? 0) < 0.5 }"
                  :style="{ width: `${Math.max(2, (row.rate ?? 0) * 100)}%` }"
                />
              </span>
              <span class="tji__fail">{{ failureLine(row) }}</span>
            </li>
          </ul>
          <BaseEmptyState v-else size="compact" icon="mdi-test-tube-empty" :title="t('tasks.judgeInsights.noCases')" />
        </section>

        <section>
          <h4 class="tji__h">{{ t('tasks.judgeInsights.spreadTitle') }}</h4>
          <div class="tji__hist" data-testid="insights-judge-spread">
            <div v-for="(count, i) in stats.buckets" :key="i" class="tji__col" :title="`${bucketLabels[i]}: ${count}`">
              <span class="tji__col-n">{{ count || '' }}</span>
              <span class="tji__col-bar" :style="{ height: `${(count / bucketMax) * 100}%` }" />
              <span class="tji__col-label">{{ bucketLabels[i] }}</span>
            </div>
          </div>
          <p class="tji__note">
            {{
              t('tasks.judgeInsights.spreadNote', {
                judged: stats.judged,
                scoreFailed: stats.scoreFailed,
                failed: stats.failed,
                inFlight: stats.inFlight,
                notJudged: stats.notJudged,
              })
            }}
          </p>
        </section>
      </div>
    </template>
  </PanelCard>
</template>

<style scoped lang="scss">
@use '../../styles/breakpoints.scss' as bp;

.tji__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.tji__gap {
  margin-bottom: 12px;
}

.tji__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
}

@include bp.below(bp.$bp-mobile) {
  .tji__grid {
    grid-template-columns: 1fr;
  }
}

.tji__h {
  margin: 0 0 10px;
  font-size: 0.85rem;
  font-weight: 600;
}

.tji__cases {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 420px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.tji__case {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tji__case-top {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 0.82rem;
}

.tji__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tji__tag {
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: var(--muted);
  font-size: 0.7rem;
}

.tji__count {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tji__track {
  height: 8px;
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.tji__track i {
  display: block;
  height: 100%;
  background: rgba(var(--v-theme-on-surface), 0.45);
  border-radius: 999px;
}

.tji__track i.tji__bar--low {
  background: rgb(var(--v-theme-error));
}

.tji__fail {
  color: var(--muted);
  font-size: 0.75rem;
}

.tji__hist {
  display: grid;
  grid-template-columns: repeat(10, minmax(0, 1fr));
  gap: 4px;
  align-items: end;
  height: 180px;
}

.tji__col {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  height: 100%;
  gap: 4px;
}

.tji__col-n {
  font-size: 0.72rem;
  font-variant-numeric: tabular-nums;
}

.tji__col-bar {
  width: 100%;
  min-height: 2px;
  background: rgba(var(--v-theme-on-surface), 0.45);
  border-radius: 4px 4px 0 0;
}

.tji__col-label {
  color: var(--muted);
  font-size: 0.62rem;
  white-space: nowrap;
}

.tji__note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
