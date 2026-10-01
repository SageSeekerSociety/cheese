<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { FeatureDays, TopicNamingReport } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMetricList from '@/components/admin/AdminMetricList.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import { fmtCost, fmtNum, fmtPercent } from '@/lib/usageFormat'
import { getTopicNamingReport } from '@/views/admin/features/featureApi'

// 功能数据的第二页：**智能命名**（`/admin/feature-stats/topic-naming`）。话题在后台被
// 自动取标题，这一页回答的是**「这件事值不值得继续开着」**：花了多少（调用、token、
// 花费）、这些调用成不成（网关的成功率）、它写了多少个名字、人后来改掉了多少。
//
// 三件读法上的事，决定了版式：
//
// 1. **「调用成功率」不是「命名成功率」。** 网关眼里的成功是「请求打到了模型并回来了」；
//    模型回一段截断的、或者包坏了的答案，在网关那里同样是 200。后者只写在日志里，没有
//    进任何表 —— 所以这一页没有「命名成功率」这个数，标签就写成网关那个口径（见后端
//    `feature_stats/features/topic_naming.py` 的文件头）。编一个更动听的名字，读的人就
//    会拿它当模型质量。
// 2. **读不到网关时是破折号，不是 0。** 网关那一半数全是 `null`，标签换成「读不到」，
//    配一句为什么 —— 画 0 读起来是「这个月没花钱」。
// 3. **「被人改掉」带着分母。** 这个比例的分母是**被自动命名过的房间**，不是全部房间：
//    人自己起的名字不算平台被改掉。分母和分子都画出来。
defineOptions({ name: 'TopicNamingPage' })

const { t } = useI18n()

const days = ref<FeatureDays>(30)
const report = ref<TopicNamingReport | null>(null)
const loading = ref(true)
const failed = ref(false)

/** 页签的值走字符串 —— `AdminTabs` 是 `T extends string` 的泛型；回到 `setDays` 里再
 *  收成数字，`days` 是发给接口的参数，别让它变成字符串。 */
const dayOptions = computed(() => [
  { value: '7', label: t('featureStats.days.7') },
  { value: '30', label: t('featureStats.days.30') },
  { value: '90', label: t('featureStats.days.90') },
])

function setDays(value: string) {
  days.value = Number(value) as FeatureDays
}

/** 整数：调用、标题个数都不存在半个。空串（没读到）交给卡片画破折号。 */
const count = (value: number | null | undefined) =>
  value === null || value === undefined ? '' : fmtNum(Math.round(value))

const money = (value: number | null | undefined) => (value === null || value === undefined ? '' : fmtCost(value))

// 切换窗口后，旧请求晚到也不能覆盖当前窗口。
let requestId = 0
async function load() {
  const current = ++requestId
  loading.value = true
  failed.value = false
  try {
    const next = await getTopicNamingReport(days.value)
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

// 切窗口**重新取数**（窗口是服务端的问法，`?days=`），不是在这边筛已到的行：折线是
// 服务端按天补 0 补出来的，这边再筛一遍就得把那份补 0 的逻辑抄一遍。
watch(days, load)
onMounted(load)

const numbers = computed(() => report.value?.numbers ?? null)

/** 两条线，两个动作：平台写的、人改的。线型是约定的第二重信号（灰度截图里也在）。
 *  同一个纵轴 —— 把两个量画在两把尺子上，谁高谁低就成了错觉。 */
const series = computed<ChartSeries[]>(() => [
  {
    name: t('featureStats.naming.trend.auto'),
    values: report.value?.trend.map((point) => point.auto) ?? [],
    style: 'solid',
  },
  {
    name: t('featureStats.naming.trend.person'),
    values: report.value?.trend.map((point) => point.person) ?? [],
    style: 'dashed',
  },
])

/** 横轴只写月-日：窗口再长也只有 90 天，年份在同一页上不会变。 */
const xLabels = computed(() => report.value?.trend.map((point) => point.date.slice(5)) ?? [])

/* 各张卡的第三行（`delta` 那个道具）。都先判有没有值再拼句子：**没有值的那一行整个
   不画**，不画成「0 / 0」——那读起来是「统计过了，是零」。 */
const callsFailed = computed(() => {
  const failedCount = numbers.value?.calls.failed
  return failedCount === null || failedCount === undefined
    ? ''
    : t('featureStats.naming.kpi.callsFailed', { failed: fmtNum(failedCount) })
})

const callsOf = computed(() => {
  const calls = numbers.value?.calls
  if (!calls || calls.value === null) return ''
  return t('featureStats.naming.kpi.callsOf', {
    failed: fmtNum(calls.failed ?? 0),
    total: fmtNum(calls.value),
  })
})

const tokenSplit = computed(() => {
  const tokens = numbers.value?.tokens
  if (!tokens || tokens.value === null) return ''
  return t('featureStats.naming.kpi.tokenSplit', {
    prompt: fmtNum(tokens.prompt ?? 0),
    completion: fmtNum(tokens.completion ?? 0),
    cache: fmtNum(tokens.cache_read ?? 0),
  })
})

/** 额度那一句：读不到网关时没有它（没有值的那一行不画），读到了就说清累计花了多少、
 *  上限是多少 —— 这是那个会让命名静默停下的数。 */
const budget = computed(() => {
  const cost = numbers.value?.cost
  if (!cost || cost.budget_usd === null || cost.budget_usd === undefined) return ''
  return t('featureStats.naming.kpi.budget', {
    spend: fmtCost(cost.key_spend_usd ?? 0),
    budget: fmtCost(cost.budget_usd),
  })
})

const stages = computed(() => {
  const renames = numbers.value?.renames
  if (!renames) return ''
  return t('featureStats.naming.kpi.stages', {
    name: fmtNum(renames.name),
    calibrate: fmtNum(renames.calibrate),
    follow: fmtNum(renames.follow),
  })
})

const overriddenOf = computed(() => {
  const overridden = numbers.value?.overridden
  if (!overridden || overridden.named === 0) return ''
  return t('featureStats.naming.kpi.overriddenOf', {
    value: fmtNum(overridden.value),
    named: fmtNum(overridden.named),
  })
})

/** 花费那张卡的标签：读不到网关时**不能**只画一个破折号 —— 那看起来像「还没加载」。
 *  网关答了话、但上面没有那把密钥又是另一句话：一个数都没有报零，可这两句话说的不
 *  是一件事，得分开说。 */
const costSource = computed(() => numbers.value?.cost.source)

const costLabel = computed(() => {
  if (costSource.value === 'unavailable') return t('featureStats.naming.kpi.costUnavailable')
  if (costSource.value === 'no-key') return t('featureStats.naming.kpi.costNoKey')
  return t('featureStats.naming.kpi.cost')
})

const costNote = computed(() => {
  if (costSource.value === 'unavailable') return t('featureStats.naming.kpi.costUnknownNote')
  if (costSource.value === 'no-key') return t('featureStats.naming.kpi.costNoKeyNote')
  return t('featureStats.naming.kpi.costNote')
})

const window = computed(() =>
  report.value ? t('featureStats.page.window', { start: report.value.start, end: report.value.end }) : ''
)

const stageRows = computed(() => {
  const renames = report.value?.numbers.renames
  return [
    { label: t('featureStats.naming.stages.name'), value: count(renames?.name) },
    { label: t('featureStats.naming.stages.calibrate'), value: count(renames?.calibrate) },
    { label: t('featureStats.naming.stages.follow'), value: count(renames?.follow) },
  ]
})

const personRows = computed(() => {
  const edits = report.value?.numbers.person_edits
  return [
    { label: t('featureStats.naming.person.rename'), value: count(edits?.rename) },
    { label: t('featureStats.naming.person.undo'), value: count(edits?.undo) },
  ]
})
</script>

<template>
  <div class="anaming">
    <AdminPageHeader
      :title="t('featureStats.features.topicNaming.title')"
      :sub="t('featureStats.features.topicNaming.summary')"
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
    </AdminPageHeader>

    <div class="anaming__inner page-container--admin">
      <p v-if="window" class="anaming__stamp t-meta-read">{{ window }}</p>

      <AdminEmptyState
        v-if="failed"
        :title="t('featureStats.page.loadFailed')"
        :action="t('featureStats.page.retry')"
        tone="error"
        @action="load"
      />

      <template v-else>
        <div class="anaming__kpis">
          <AdminKpiCard
            :label="t('featureStats.naming.kpi.calls')"
            :value="count(numbers?.calls.value ?? null)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.naming.kpi.callsNote')"
            :delta="callsFailed"
            :delta-title="t('featureStats.naming.kpi.callsNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.naming.kpi.successRate')"
            :value="fmtPercent(numbers?.calls.success_rate ?? null)"
            :loading="loading"
            :note="t('featureStats.naming.kpi.successRateNote')"
            :delta="callsOf"
            :delta-title="t('featureStats.naming.kpi.successRateNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.naming.kpi.tokens')"
            :value="count(numbers?.tokens.value ?? null)"
            :loading="loading"
            :note="t('featureStats.naming.kpi.tokensNote')"
            :delta="tokenSplit"
            :delta-title="t('featureStats.naming.kpi.tokensNote')"
          />
          <AdminKpiCard
            :label="costLabel"
            :value="money(numbers?.cost.usd ?? null)"
            :loading="loading"
            :note="costNote"
            :delta="budget"
            :delta-title="costNote"
          />
          <AdminKpiCard
            :label="t('featureStats.naming.kpi.renames')"
            :value="count(numbers?.renames.value ?? null)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.naming.kpi.renamesNote')"
            :delta="stages"
            :delta-title="t('featureStats.naming.kpi.renamesNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.naming.kpi.overridden')"
            :value="fmtPercent(numbers?.overridden.share ?? null)"
            :loading="loading"
            :note="t('featureStats.naming.kpi.overriddenNote')"
            :delta="overriddenOf"
            :delta-title="t('featureStats.naming.kpi.overriddenNote')"
          />
        </div>

        <div class="anaming__row">
          <AdminLineChart
            :title="t('featureStats.naming.trend.title')"
            :note="t('featureStats.naming.trend.note')"
            :x-labels="xLabels"
            :series="series"
            :loading="loading"
          />
        </div>

        <div class="anaming__row anaming__row--equal">
          <AdminMetricList
            :title="t('featureStats.naming.stages.title')"
            :note="t('featureStats.naming.stages.note')"
            :caption="t('featureStats.naming.stages.caption', { n: fmtNum(report?.numbers.renames.value ?? 0) })"
            :rows="stageRows"
            :emphasis="t('featureStats.naming.stages.name')"
            :loading="loading"
          />
          <AdminMetricList
            :title="t('featureStats.naming.person.title')"
            :note="t('featureStats.naming.person.note')"
            :caption="t('featureStats.naming.person.caption', { n: fmtNum(report?.numbers.person_edits.value ?? 0) })"
            :rows="personRows"
            :loading="loading"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 滚动归这一页自己领（同 `AdminDashboardPage` 的 `.ad` 与 `DocsAssistantPage` 的
   `.adoc`），页头的负 margin 同理。 */
.anaming {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 0 24px 24px;
  overflow-y: auto;
}

.anaming :deep(.aph) {
  margin: 0 -24px;
}

.anaming__inner {
  display: flex;
  flex-direction: column;
  margin: 0 auto;
  container-type: inline-size;
}

/* 窗口那行（「统计窗口：2026-09-01 至 2026-09-30」）：它是这一页所有数的口径，
   放在所有数上面一行。常驻不收起。 */
.anaming__stamp {
  margin: 12px 0 0;
  color: var(--muted);
}

/* KPI 行：格与格之间用发丝线（每格自己的上/左边线 + 面板负 margin 把第一行第一列的
   推出外框裁掉），和看板那一排同一个做法。 */
.anaming__kpis {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 16px 0 -1px -1px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

@container (min-width: 560px) {
  .anaming__kpis {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

/* 窄屏两列时落单的末位铺满整行（2 列 6 张正好 3 行，5 张时不留半格洞）。 */
@container (max-width: 559px) {
  .anaming__kpis > :deep(*:last-child:nth-child(odd)) {
    grid-column: 1 / -1;
  }
}

.anaming__kpis > :deep(*) {
  border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
}

.anaming__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

/* 两块并排的起始宽度和看板一样是 720：再窄，两张卡片各自的数字会互相压。 */
@container (min-width: 720px) {
  .anaming__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}
</style>
