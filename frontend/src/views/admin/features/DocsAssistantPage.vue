<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { DocsAssistantReport, FeatureDays } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminHistogram from '@/components/admin/AdminHistogram.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMetricList from '@/components/admin/AdminMetricList.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminQuestionTable from '@/components/admin/AdminQuestionTable.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { fmtCost, fmtMs, fmtNum, fmtPercent } from '@/lib/usageFormat'
import { getDocsAssistantReport } from '@/views/admin/features/featureApi'

// 功能数据的第一页：**问芝士**（文档站的问答助手，`/admin/feature-stats/docs-assistant`）。
//
// 这一页要回答的不是「有多少人读了文档」，而是**「问芝士」这四个字值不值**：有人来吗
// （访客）、来了之后用吗（提问者占登录访客的比例）、用起来答得上吗（回答成功率）、
// 答一次花多少（token 分布与耗时）、答不上来的是哪些问题（那是一份给写文档的人的清单）。
// 五件事缺一个都答不了那个问题，所以它们在同一页上，按这个顺序从上往下。
//
// 三件**读法**上的事，写在这里因为它们决定了版式：
//
// 1. **比例的分母写出来。** 「12 人提问」和「占登录访客 40%」两个都画 —— 只画比例的话，
//    访客掉一半这件事在图上是「提问的人变多了」。
// 2. **花费是估算，页面上标着「估算」。** 这个虚拟密钥拿不到按窗口的真实账单（详见
//    后端 `feature_stats/pricing.py`：网关只有全表扫描的 `/spend/logs` 和一条累计的
//    `/key/info`），所以这一格是按网关价目表算出来的，标签必须说出来。读不到价目表时
//    它是长破折号，不是 $0 —— 0 读作「免费」，那是另一个意思。
// 3. **`delta` 这一个道具在这页上放的是「分母」不是「环比」。** 那张卡第三行本来就是
//    一行灰色小字，`deltaTitle` 正好承担口径那句话；这一页没有上一个窗口的数据可比
//    （后端不给），所以与其空着，不如让它放「占登录访客 40%」这种必须写在旁边才读得
//    懂的数。位置一样、读法一样，只是名字里的「环比」在这页不成立。
defineOptions({ name: 'DocsAssistantPage' })

const { t } = useI18n()

const days = ref<FeatureDays>(30)
const report = ref<DocsAssistantReport | null>(null)
const loading = ref(true)
const failed = ref(false)

/** 页签的值走字符串 —— `AdminTabs` 是 `T extends string` 的泛型（同 `AdminModelsPage`）；
 *  回到 `setDays` 里再收成数字，`days` 是发给接口的参数，别让它变成字符串。 */
const dayOptions = computed(() => [
  { value: '7', label: t('featureStats.days.7') },
  { value: '30', label: t('featureStats.days.30') },
  { value: '90', label: t('featureStats.days.90') },
])

/** 换窗口：页签给的是字符串（见 `dayOptions`），接口要的是数字。 */
function setDays(value: string) {
  days.value = Number(value) as FeatureDays
}

/** 比例那一句里的百分比。`fmtPercent` 的规则照用（小于 0.1% 写 `<0.1%`，不写 `0%`：
 *  有一个和没有一个是两句话），只是 `null`（没有分母）在这里要给**空串**，让卡片
 *  那一行整个不画，而不是画一个破折号。 */
function shareText(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? '' : fmtPercent(rate)
}

/** token 和次数都是整数量：`describe` 回的是浮点（平均、中位会带小数），四舍五入到
 *  整数再分组。半毫秒、半个 token 这种东西不存在，画出来只会让人以为量得很细。 */
const count = (value: number | null) => (value === null || value === undefined ? '' : fmtNum(Math.round(value)))

/** 「答一次花了多久」那一组：毫秒/秒/分三档由 `fmtMs` 定（850 ms、1.2 s、2 min）——
 *  一屏五个数混着看时，单位跟着量级走比一行全是五位数好读。 */
const ms = (value: number | null) => fmtMs(value)

const money = (value: number | null) => (value === null || value === undefined ? '' : fmtCost(value))

async function load() {
  loading.value = true
  failed.value = false
  try {
    report.value = await getDocsAssistantReport(days.value)
  } catch {
    report.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}

// 切窗口**重新取数**（窗口是服务端的问法，`?days=`），不是在这边筛已到的行：曲线是
// 服务端按天补 0 补出来的，这边再筛一遍就得把那份补 0 的逻辑抄一遍。
watch(days, load)
onMounted(load)

const numbers = computed(() => report.value?.numbers ?? null)

/** 三条线的线型是**约定的第二重信号**（灰度截图里也在，同 `AdminLineChart` 文件头）：
 *  最深最实的线是「来了多少人」，其次「多少人问了」，最浅的是「问了多少次」。 */
const series = computed<ChartSeries[]>(() => [
  {
    name: t('featureStats.trend.visitors'),
    values: report.value?.trend.map((point) => point.visitors) ?? [],
    style: 'solid',
  },
  {
    name: t('featureStats.trend.askers'),
    values: report.value?.trend.map((point) => point.askers) ?? [],
    style: 'dashed',
  },
  {
    name: t('featureStats.trend.questions'),
    values: report.value?.trend.map((point) => point.questions) ?? [],
    style: 'dotted',
  },
])

/** 横轴只写月-日：窗口再长也只有 90 天，年份在同一页上不会变，写出来是六列重复的字。 */
const xLabels = computed(() => report.value?.trend.map((point) => point.date.slice(5)) ?? [])

/* 第三行那几颗小字（`delta` 那个道具，见文件头第 3 条）。口径都写在各自卡片的 note 里，
   这里只给数。 */
const loggedInShare = computed(() => {
  const visitors = numbers.value?.visitors
  if (!visitors || visitors.value === 0) return ''
  return t('featureStats.kpi.loggedInShare', { percent: fmtPercent(visitors.logged_in / visitors.value) })
})

const askerShare = computed(() => {
  const share = shareText(numbers.value?.askers.share)
  return share ? t('featureStats.kpi.askersShare', { percent: share }) : ''
})

const perAsker = computed(() => {
  const per = numbers.value?.questions.per_asker
  return per === null || per === undefined ? '' : t('featureStats.kpi.perAsker', { value: per.toFixed(1) })
})

const answeredOf = computed(() => {
  const rate = numbers.value?.answer_rate
  return rate ? t('featureStats.kpi.answeredOf', { answered: fmtNum(rate.answered), total: fmtNum(rate.total) }) : ''
})

const perQuestion = computed(() => {
  const per = numbers.value?.cost.per_question
  return per === null || per === undefined ? '' : t('featureStats.kpi.perQuestion', { value: fmtCost(per) })
})

/** 花费那张卡的标签：读不到价目表时**不能**只画一个破折号 —— 那看起来像「还没加载」。
 *  标签本身换成「估算不了」，再配一句为什么（note 里）。 */
const costLabel = computed(() =>
  numbers.value?.cost.source === 'unavailable' ? t('featureStats.kpi.costUnavailable') : t('featureStats.kpi.cost')
)

const costNote = computed(() =>
  numbers.value?.cost.source === 'unavailable'
    ? t('featureStats.kpi.costUnknownNote')
    : t('featureStats.kpi.costNote', { tokens: fmtNum(numbers.value?.cost.unpriced_tokens ?? 0) })
)

const window = computed(() =>
  report.value ? t('featureStats.page.window', { start: report.value.start, end: report.value.end }) : ''
)

const tokenRows = computed(() => {
  const tokens = report.value?.tokens
  return [
    { label: t('featureStats.metric.avg'), value: count(tokens?.avg ?? null) },
    { label: t('featureStats.metric.median'), value: count(tokens?.median ?? null) },
    { label: t('featureStats.metric.p90'), value: count(tokens?.p90 ?? null) },
    { label: t('featureStats.metric.max'), value: count(tokens?.max ?? null) },
    { label: t('featureStats.metric.min'), value: count(tokens?.min ?? null) },
  ]
})

const latencyRows = computed(() => {
  const latency = report.value?.latency
  return [
    { label: t('featureStats.metric.avg'), value: ms(latency?.avg ?? null) },
    { label: t('featureStats.metric.median'), value: ms(latency?.median ?? null) },
    { label: t('featureStats.metric.p90'), value: ms(latency?.p90 ?? null) },
    { label: t('featureStats.metric.max'), value: ms(latency?.max ?? null) },
    { label: t('featureStats.metric.min'), value: ms(latency?.min ?? null) },
  ]
})

const outcomeSegments = computed(() => {
  const outcomes = report.value?.outcomes
  return [
    { label: t('featureStats.outcomes.answered'), value: outcomes?.answered ?? 0, shade: 'ink' as const },
    { label: t('featureStats.outcomes.noMatch'), value: outcomes?.no_match ?? 0, shade: 'muted' as const },
    { label: t('featureStats.outcomes.failed'), value: outcomes?.failed ?? 0, shade: 'faint' as const },
  ]
})
</script>

<template>
  <AdminPage
    :title="t('featureStats.features.docsAssistant.title')"
    :sub="t('featureStats.features.docsAssistant.summary')"
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

    <div class="adoc__body admin-page__body">
      <p v-if="window" class="adoc__stamp t-meta-read">{{ window }}</p>

      <BaseLoadError
        v-if="failed"
        :title="t('featureStats.page.loadFailed')"
        :retry-label="t('featureStats.page.retry')"
        @retry="load"
      />

      <template v-else>
        <div class="admin-kpi-grid">
          <AdminKpiCard
            :label="t('featureStats.kpi.visitors')"
            :value="count(numbers?.visitors.value ?? null)"
            :unit="t('featureStats.unit.people')"
            :loading="loading"
            :note="t('featureStats.kpi.visitorsNote')"
            :spark="report?.trend.map((point) => point.visitors) ?? []"
          />
          <AdminKpiCard
            :label="t('featureStats.kpi.loggedIn')"
            :value="count(numbers?.visitors.logged_in ?? null)"
            :unit="t('featureStats.unit.people')"
            :loading="loading"
            :note="t('featureStats.kpi.loggedInNote')"
            :delta="loggedInShare"
            :delta-title="t('featureStats.kpi.loggedInNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.kpi.askers')"
            :value="count(numbers?.askers.value ?? null)"
            :unit="t('featureStats.unit.people')"
            :loading="loading"
            :note="t('featureStats.kpi.askersNote')"
            :delta="askerShare"
            :delta-title="t('featureStats.kpi.askersNote')"
            :spark="report?.trend.map((point) => point.askers) ?? []"
          />
          <AdminKpiCard
            :label="t('featureStats.kpi.questions')"
            :value="count(numbers?.questions.value ?? null)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.kpi.questionsNote')"
            :delta="perAsker"
            :delta-title="t('featureStats.kpi.questionsNote')"
            :spark="report?.trend.map((point) => point.questions) ?? []"
          />
          <AdminKpiCard
            :label="t('featureStats.kpi.answerRate')"
            :value="fmtPercent(numbers?.answer_rate.value ?? null)"
            :loading="loading"
            :note="t('featureStats.kpi.answerRateNote')"
            :delta="answeredOf"
            :delta-title="t('featureStats.kpi.answerRateNote')"
          />
          <AdminKpiCard
            :label="costLabel"
            :value="money(numbers?.cost.usd ?? null)"
            :loading="loading"
            :note="costNote"
            :delta="perQuestion"
            :delta-title="costNote"
          />
        </div>

        <div class="adoc__row">
          <AdminLineChart
            :title="t('featureStats.trend.title')"
            :note="t('featureStats.trend.note')"
            :x-labels="xLabels"
            :series="series"
            :loading="loading"
          />
        </div>

        <div class="adoc__row adoc__row--equal">
          <AdminMetricList
            :title="t('featureStats.tokens.title')"
            :note="t('featureStats.tokens.note')"
            :caption="t('featureStats.tokens.caption', { n: fmtNum(report?.tokens.count ?? 0) })"
            :rows="tokenRows"
            :emphasis="t('featureStats.metric.median')"
            :loading="loading"
          />
          <AdminHistogram
            :title="t('featureStats.histogram.title')"
            :note="t('featureStats.histogram.note')"
            :unit="t('featureStats.unit.chars')"
            :rows="report?.tokens.histogram ?? []"
            :loading="loading"
          />
        </div>

        <div class="adoc__row adoc__row--equal">
          <AdminMetricList
            :title="t('featureStats.latency.title')"
            :note="t('featureStats.latency.note')"
            :caption="t('featureStats.latency.caption', { n: fmtNum(report?.latency.count ?? 0) })"
            :rows="latencyRows"
            :emphasis="t('featureStats.metric.median')"
            :loading="loading"
          />
          <AdminShareBar
            :title="t('featureStats.outcomes.title')"
            :note="t('featureStats.outcomes.note')"
            :segments="outcomeSegments"
            :loading="loading"
          />
        </div>

        <div class="adoc__row">
          <AdminQuestionTable
            :title="t('featureStats.unanswered.title')"
            :note="t('featureStats.unanswered.note')"
            :empty="t('featureStats.unanswered.empty')"
            :rows="report?.unanswered ?? []"
            :loading="loading"
          />
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 窗口那行（「统计窗口：2026-09-01 至 2026-09-30」）：它是这一页所有数的口径，
   放在所有数上面一行。常驻不收起。 */
.adoc__stamp {
  margin: 0;
  color: var(--muted);
}

.adoc__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

/* 两块并排的起始宽度和看板一样是 720：再窄，直方图的三列和折线的刻度会互相压。 */
@container (min-width: 720px) {
  .adoc__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}
</style>
