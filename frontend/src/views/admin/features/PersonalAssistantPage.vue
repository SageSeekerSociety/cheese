<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { FeatureDays, PersonalAssistantReport } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMetricList from '@/components/admin/AdminMetricList.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { fmtCost, fmtNum, fmtPercent } from '@/lib/usageFormat'
import { getPersonalAssistantReport } from '@/views/admin/features/featureApi'

// 功能数据：**个人芝士**（`/admin/feature-stats/personal-assistant`）。回答两件事：
//
// 1. 把关拦得对不对、花了平台多少：芝士以用户名义做别人看得到的事之前都过一次把关，
//    放行的直接做，先问的变成卡片。先问的比例、各在哪一步定下、各类动作分开看。
// 2. 对话的开头稳不稳：每次回答的模型调用里，提示词被服务方缓存接住多少。改规矩、改
//    工具之后先看这个数。
defineOptions({ name: 'PersonalAssistantPage' })

const { t } = useI18n()

const days = ref<FeatureDays>(30)
const report = ref<PersonalAssistantReport | null>(null)
const loading = ref(true)
const failed = ref(false)

const dayOptions = computed(() => [
  { value: '7', label: t('featureStats.days.7') },
  { value: '30', label: t('featureStats.days.30') },
  { value: '90', label: t('featureStats.days.90') },
])

function setDays(value: string) {
  days.value = Number(value) as FeatureDays
}

let requestId = 0
async function load() {
  const current = ++requestId
  loading.value = true
  failed.value = false
  try {
    const next = await getPersonalAssistantReport(days.value)
    if (current === requestId) report.value = next
  } catch {
    if (current === requestId) {
      report.value = null
      failed.value = true
    }
  } finally {
    if (current === requestId) loading.value = false
  }
}

watch(days, load)
onMounted(load)

const gate = computed(() => report.value?.gate ?? null)
const answers = computed(() => report.value?.answers ?? null)
const count = (value: number | null | undefined) =>
  value === null || value === undefined ? '' : fmtNum(Math.round(value))

const askedShare = computed(() => (gate.value && gate.value.checks ? gate.value.asked / gate.value.checks : null))

const series = computed<ChartSeries[]>(() => [
  {
    name: t('featureStats.assistant.trend.checks'),
    values: report.value?.daily.map((d) => d.checks) ?? [],
    style: 'solid',
  },
  {
    name: t('featureStats.assistant.trend.asked'),
    values: report.value?.daily.map((d) => d.asked) ?? [],
    style: 'dashed',
  },
])
const xLabels = computed(() => report.value?.daily.map((d) => d.date.slice(5)) ?? [])

const STAGES = [
  'asked:rule',
  'allowed:low',
  'allowed:quick',
  'allowed:reasoned',
  'asked:reasoned',
  'asked:error',
] as const
const stageRows = computed(() =>
  STAGES.map((key) => ({
    label: t(`featureStats.assistant.stages.${key}`),
    value: count(gate.value?.byStage[key] ?? 0),
  }))
)

const kindRows = computed(() =>
  (gate.value?.byKind ?? []).map((k) => ({
    label: t(`featureStats.assistant.kinds.${k.kind}`),
    value: t('featureStats.assistant.kinds.row', { allowed: fmtNum(k.allowed), asked: fmtNum(k.asked) }),
  }))
)
</script>

<template>
  <AdminPage
    :title="t('featureStats.features.personalAssistant.title')"
    :sub="t('featureStats.features.personalAssistant.summary')"
  >
    <template #tools>
      <AdminTabs
        size="sm"
        :label="t('featureStats.page.windowAria')"
        :model-value="String(days)"
        :options="dayOptions"
        @update:model-value="setDays"
      />
    </template>
    <div class="apa__body admin-page__body">
      <BaseLoadError
        v-if="failed"
        :title="t('featureStats.page.loadFailed')"
        :retry-label="t('featureStats.page.retry')"
        @retry="load"
      />
      <template v-else>
        <div class="admin-kpi-grid">
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.checks')"
            :value="count(gate?.checks)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.checksNote')"
            :delta="
              gate
                ? t('featureStats.assistant.kpi.checksSplit', {
                    allowed: fmtNum(gate.allowed),
                    asked: fmtNum(gate.asked),
                  })
                : ''
            "
          />
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.askedShare')"
            :value="fmtPercent(askedShare)"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.askedNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.cost')"
            :value="gate ? fmtCost(gate.costUsd) : ''"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.costNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.gateCache')"
            :value="fmtPercent(gate?.cacheRate ?? null)"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.gateCacheNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.answerCache')"
            :value="fmtPercent(answers?.cacheRate ?? null)"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.answerCacheNote')"
            :delta="
              answers
                ? t('featureStats.assistant.kpi.answerCacheSplit', {
                    answers: fmtNum(answers.answers),
                    calls: fmtNum(answers.calls),
                  })
                : ''
            "
          />
          <AdminKpiCard
            :label="t('featureStats.assistant.kpi.latency')"
            :value="count(gate?.latencyMs.median)"
            unit="ms"
            :loading="loading"
            :note="t('featureStats.assistant.kpi.latencyNote')"
            :delta="
              gate?.latencyMs.p90 != null
                ? t('featureStats.assistant.kpi.latencyP90', { p90: fmtNum(Math.round(gate.latencyMs.p90)) })
                : ''
            "
          />
        </div>
        <div class="apa__row">
          <AdminLineChart
            :title="t('featureStats.assistant.trend.title')"
            :note="t('featureStats.assistant.trend.note')"
            :x-labels="xLabels"
            :series="series"
            :loading="loading"
          />
        </div>
        <div class="apa__row apa__row--equal">
          <AdminMetricList
            :title="t('featureStats.assistant.stages.title')"
            :note="t('featureStats.assistant.stages.note')"
            :rows="stageRows"
            :loading="loading"
          />
          <AdminMetricList
            :title="t('featureStats.assistant.kinds.title')"
            :note="t('featureStats.assistant.kinds.note')"
            :rows="kindRows"
            :loading="loading"
          />
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
.apa__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

@container (min-width: 720px) {
  .apa__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}
</style>
