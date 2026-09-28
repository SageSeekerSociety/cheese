<script setup lang="ts">
// 整板看板 —— 管理员的问题：「**这块板**怎么样」。
//
// 与单题看板（`TaskInsights.vue`）的分工：那一页回答「我这道题卡在哪儿」（出题人的
// 问题，有领取者名单、有一个人走到哪一步）；这一页没有那些，只有跨题的汇总 ——
// 六个 KPI、领取与提交的走势、题目构成、分类分布、最热的题、出题人排行，以及
// 「有什么要我做」的那四格。两块看板各说各的，别把单题的内容搬过来。
//
// 数据全部来自**真接口**（老树那九页背后同一组 `/spaces/{id}/analytics/*`，后端挂在
// `_ensure_space_admin` 后面，所以普通成员连请求都发不出去）：
// - `GET /spaces/{id}/analytics/overview` —— KPI、题目构成、分类分布、走势
// - `GET /spaces/{id}/analytics/alerts` —— 待审、领了不动这类治理数字
// - `GET /spaces/{id}/analytics/tasks` —— 逐题一行（最热的题、三天内截止、无人领取）
// - `GET /spaces/{id}/analytics/publishers` —— 出题人那一行
//
// 三条口径写在这里，页脚也照说一遍 —— 它们是这一屏最容易被当成 bug 的地方：
// 1. **窗口**。这一组接口默认只看最近 180 天（后端 `DEFAULT_WINDOW_DAYS`），题目按
//    **创建时间**落在窗口里。窗口不写死在页面上，由响应里的 `summary.from/to` 回来说，
//    页面照它显示。
// 2. **主体**。KPI 里的「领取 / 提交 / 通过」都按**主体**算（个人或一支小队各算一个），
//    与老树九页同一口径；所以这里没有原型那一对「参与人数」与「领取总数」—— 真库里
//    它们是同一个数（memberships 的行数），画两张卡是把一个数说两遍。
// 3. **领了没动**。原型那一格是「**人**领了五天没动」的名单；接口给的是「**题**上两周
//    没有提交」的条数（后端 14 天口径），逐人的那一层它不返回。所以那一格只有数字、
//    没有名单 —— 少画，不编。
//
// 五个 tab 里的「题目 / 参与者 / 出题人」三格照原型排，但**只在接口真有那一层的
// 地方画那一层**（原型的数据是造的，它算得出来的不一定真有来源）：
// - 题目：`/analytics/tasks` 逐题一行，全在。
// - 出题人：`/analytics/publishers` 逐人一行，含 `successRate`（= 通过 / 提交）与
//   `avgParticipantsPerTask`，两个数接口自己算好给。
// - 参与者：`/analytics/participants` **只回聚合**（报名与完成的主体数、分布、走势），
//   不返回成员名单 —— 「这人领了几道、还在不在做」没有来源，所以那两列不画，页面上
//   如实说明。逐人的明细真平台只有一份 CSV 导出（`/analytics/participants/export`，
//   带审计日志的下载），不是这一屏该去拉的东西。
//
// 老树那九页一页没删，仍在 `/spaces/:id/analytics/*` 上原样服务：要逐题、逐人、逐
// 出题人（以及课程的学习那一格）翻明细时，走页脚那条链。
import type {
  AnalyticsTimeSeriesPoint,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipantEntityMetrics,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPublisherMetrics,
  SpaceAnalyticsTask,
} from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import BarList from '../components/BarList.vue'
import MetricCard from '../components/MetricCard.vue'
import PageBar from '../components/PageBar.vue'
import PanelCard from '../components/PanelCard.vue'
import SplitBar from '../components/SplitBar.vue'
import TrendChart from '../components/TrendChart.vue'

import { SpacesApi } from '@/network/api/spaces'

type Tone = 'ok' | 'warn' | 'danger' | 'muted'

const DAY = 86_400_000
/** 走势画最近 12 天，和原型同一段。后端按天分桶，桶的边界是 UTC 零点。 */
const TREND_DAYS = 12
/** 一页 20 条 —— 与板里其它列表（首页、我的、审核）同一个量。 */
const PAGE_SIZE = 20

const route = useRoute()
const spaceId = computed(() => Number(route.params.spaceId))

const tab = ref<'overview' | 'alerts' | 'tasks' | 'people' | 'publishers'>('overview')

const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
/** 逐题一行。接口一次给全（不分页），所以「三天内截止」「无人领取」是在全量上挑。 */
const taskRows = ref<SpaceAnalyticsTask[]>([])
const publishers = ref<SpaceAnalyticsPublisherMetrics[]>([])
/** 参与者那一格**只有聚合**：报名与完成的主体数、分布、走势。接口不返回成员名单。 */
const participants = ref<SpaceAnalyticsParticipants | null>(null)

const loading = ref(true)
const failed = ref(false)

async function load() {
  const id = spaceId.value
  if (!Number.isFinite(id) || id <= 0) return
  loading.value = true
  failed.value = false
  try {
    const [overviewRes, alertsRes, tasksRes, publishersRes, participantsRes] = await Promise.all([
      SpacesApi.getAnalyticsOverview(id, { groupBy: 'day' }),
      SpacesApi.getAnalyticsAlerts(id),
      // `sortBy` 必须显式给：这一条路由的默认值是 `publishedAt`，而后端认的排序字段
      // 里没有它（`TASK_SORT_FIELDS`），不传会直接 400 —— 接口不退回默认排序。
      SpacesApi.getAnalyticsTasks(id, { sortBy: 'participantCount', sortOrder: 'desc' }),
      // 出题人排行按「累计被领取」排，与原型那一格同义。
      SpacesApi.getAnalyticsPublishers(id, { sortBy: 'participantCount', sortOrder: 'desc' }),
      // 参与者那一格要的是「报名审批 / 完成」这组数 —— 只有这条接口有（overview 的
      // 那三张卡是领取 / 提交 / 通过，说的不是报名本身）。
      SpacesApi.getAnalyticsParticipants(id),
    ])
    overview.value = overviewRes.data
    alerts.value = alertsRes.data
    taskRows.value = tasksRes.data.tasks ?? []
    publishers.value = publishersRes.data.publishers ?? []
    participants.value = participantsRes.data
  } catch {
    // 读不到（没权限、空间没了、接口改了）就说一句，不留一屏看着像「这块板是空的」。
    overview.value = null
    alerts.value = null
    taskRows.value = []
    publishers.value = []
    participants.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}

// 换空间要重取。同一个空间重复进来也会重跑一次 —— 看板是「现在怎么样」，宁可晚
// 一秒也不要给上一次进来时的旧数。
watch(spaceId, () => load(), { immediate: true })

// --- 口径 --------------------------------------------------------------------

const pad = (n: number) => String(n).padStart(2, '0')

const utcDate = (ms: number) => {
  const d = new Date(ms)
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

const pct = (value: number) => `${Math.round((value ?? 0) * 100)}%`

/** 接口自己说的统计范围。它不说（读失败 / 空）就不显示这条线。 */
const windowText = computed(() => {
  const from = overview.value?.summary.from
  const to = overview.value?.summary.to
  if (!from || !to) return ''
  return `${utcDate(from)} ~ ${utcDate(to)}`
})

// --- 总览 --------------------------------------------------------------------

const metrics = computed(() => overview.value?.entityMetrics ?? null)

interface Kpi {
  label: string
  value: string | number
  icon: string
  hint: string
  tone?: Tone
}

const kpis = computed<Kpi[]>(() => {
  const m = metrics.value
  if (!m) return []
  const pending = alerts.value?.pendingTaskApprovalCount ?? 0
  return [
    {
      label: '题目总数',
      value: m.taskCount,
      icon: 'mdi-file-document-multiple-outline',
      hint: `${m.publisherCount} 位出题人`,
    },
    {
      label: '待审核',
      value: pending,
      icon: 'mdi-clock-outline',
      tone: pending ? 'warn' : 'muted',
      hint: pending ? '审完才会出现在板上' : '队列是空的',
    },
    {
      label: '领取主体',
      value: m.participantCount,
      icon: 'mdi-hand-extended-outline',
      hint: `已通过 ${m.approvedParticipantCount}`,
    },
    {
      label: '提交主体',
      value: m.submittedParticipantCount,
      icon: 'mdi-tray-arrow-up',
      hint: `提交率 ${pct(m.submissionConversionRate)}`,
    },
    { label: '通过主体', value: m.successfulParticipantCount, icon: 'mdi-trophy-outline', hint: '判过且通过' },
    {
      label: '完成率',
      value: pct(m.successRate),
      icon: 'mdi-progress-check',
      tone: m.successRate >= 0.6 ? 'ok' : 'warn',
      // 后端的 successRate 分母是**领取主体**，不是提交主体（见 `_compute_entity_metrics`）。
      hint: '通过 / 领取主体',
    },
  ]
})

/** 最近的 12 个日历日的起点（UTC —— 与后端分桶同一把尺，差一个时区就整体错一天）。 */
const trendDays = computed(() => {
  const now = new Date()
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return Array.from({ length: TREND_DAYS }, (_, i) => today - (TREND_DAYS - 1 - i) * DAY)
})

const trendLabels = computed(() =>
  trendDays.value.map((ms) => {
    const d = new Date(ms)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
  })
)

/** 稀疏序列铺到日历日上：后端**不返回「那天没人动」**，缺的那天是 0，不是没有。
 *  按时间戳对齐而不是按下标往前挤 —— 挤一下整条线就偏了。 */
function toDaily(points: AnalyticsTimeSeriesPoint[]): number[] {
  const byDay = new Map(points.map((p) => [p.bucket, p.count]))
  return trendDays.value.map((day) => byDay.get(day) ?? 0)
}

const trendSeries = computed(() => {
  const trends = overview.value?.trends
  return [
    { name: '每天领取', values: toDaily(trends?.participantsJoined ?? []) },
    { name: '每天提交', values: toDaily(trends?.submissionsCreated ?? []) },
  ]
})

/** 12 天里一个人都没动过就不画线：一条贴在 0 上的双线不是「数据」，是噪音。 */
const hasTrend = computed(() => trendSeries.value.some((s) => s.values.some((v) => v > 0)))

/** 状态构成。顺序与用词固定在这里 —— 后端给的是 `APPROVED` / `NONE` / `DISAPPROVED`。 */
const APPROVAL_SEGMENTS: { key: string; label: string; tone: Tone }[] = [
  { key: 'APPROVED', label: '已上板', tone: 'ok' },
  { key: 'NONE', label: '待审核', tone: 'warn' },
  { key: 'DISAPPROVED', label: '已驳回', tone: 'danger' },
]

const statusSegments = computed(() => {
  const items = overview.value?.taskDistributions.byApprovalStatus.items ?? []
  const byLabel = new Map(items.map((i) => [i.label, i.count]))
  return APPROVAL_SEGMENTS.map((s) => ({ label: s.label, count: byLabel.get(s.key) ?? 0, tone: s.tone }))
})

const categoryRows = computed(() =>
  (overview.value?.taskDistributions.byCategory.items ?? []).map((i) => ({ label: i.label, value: i.count }))
)

/** 按领取主体数排的题（接口已经排好，这里只截前 6 条，和原型同一个量）。 */
const hottest = computed(() =>
  taskRows.value.slice(0, 6).map((t) => ({ label: t.taskName, value: t.participantCount }))
)

const publisherRows = computed(() =>
  publishers.value
    .slice(0, 6)
    .map((p) => ({ label: p.publisherName, value: p.participantCount, hint: `${p.taskCount} 道题` }))
)

// --- 待处理 ------------------------------------------------------------------

const closingSoon = computed(() =>
  taskRows.value.filter(
    (t) =>
      t.approved === 'APPROVED' && t.deadline != null && t.deadline > Date.now() && t.deadline - Date.now() < 3 * DAY
  )
)

const coldTasks = computed(() => taskRows.value.filter((t) => t.approved === 'APPROVED' && t.participantCount === 0))

/** 有人领、至今零提交的题。它**是**「两周没动静」那张卡的超集（那张卡另外要求最近一次
 *  提交在 14 天以前），所以列表比卡片上的数长是正常的，副标题把这件事说出来。 */
const noSubmitTasks = computed(() =>
  taskRows.value.filter((t) => t.participantCount > 0 && t.submittedParticipantCount === 0)
)

interface AlertCard {
  key: string
  icon: string
  tone: Tone
  title: string
  detail: string
  count: number
  to?: { name: string; params: { spaceId: number } }
  cta?: string
}

const firstTitles = (titles: string[]) => titles.slice(0, 2).join('、') || '—'

const alertCards = computed<AlertCard[]>(() => {
  const pending = alerts.value?.pendingTaskApprovalCount ?? 0
  const stalled = alerts.value?.stalledTaskCount ?? 0
  return [
    {
      key: 'pending',
      icon: 'mdi-clipboard-check-outline',
      tone: 'warn',
      title: `${pending} 道题在等你审`,
      detail: pending ? '审过才会上板，作者一直在等。' : '审核队列是空的。',
      count: pending,
      to: { name: 'SpaceBoardReview', params: { spaceId: spaceId.value } },
      cta: '去审核',
    },
    {
      key: 'closing',
      icon: 'mdi-timer-outline',
      tone: 'danger',
      title: `${closingSoon.value.length} 道题三天内截止`,
      detail: firstTitles(closingSoon.value.map((t) => t.taskName)),
      count: closingSoon.value.length,
    },
    {
      key: 'stalled',
      icon: 'mdi-account-clock-outline',
      tone: 'warn',
      title: `${stalled} 道题领了没交、两周没动静`,
      detail: '有已通过的领取者，但最近一次提交在 14 天以前（或从来没交过）。',
      count: stalled,
    },
    {
      key: 'cold',
      icon: 'mdi-snowflake',
      tone: 'muted',
      title: `${coldTasks.value.length} 道题上板后无人领取`,
      detail: firstTitles(coldTasks.value.map((t) => t.taskName)),
      count: coldTasks.value.length,
    },
  ]
})

const alertTotal = computed(() => alertCards.value.reduce((n, c) => n + c.count, 0))

function taskTo(taskId: number) {
  return { name: 'SpaceBoardTaskDetail', params: { spaceId: String(spaceId.value), taskId: String(taskId) } }
}

// --- 题目 --------------------------------------------------------------------

/** 一页 20 条。翻页只切一刀，不改变排序口径 —— 排序是接口给的（按领取人数降序）。 */
const taskPage = ref(1)

const pagedTaskRows = computed(() => taskRows.value.slice((taskPage.value - 1) * PAGE_SIZE, taskPage.value * PAGE_SIZE))

// 题目被审掉、总数缩水时页码收回最后一页，不停在一张空表上。
watch(taskRows, () => {
  const last = Math.max(1, Math.ceil(taskRows.value.length / PAGE_SIZE))
  if (taskPage.value > last) taskPage.value = last
})

/** 状态。接口只给 `approved`（`APPROVED` / `NONE` / `DISAPPROVED`），「已截止」是
 *  过审 + 过了截止日推出来的，与板里其它列表（`Mine.vue` 的 `publishedState`）同一套说法。 */
function taskState(t: SpaceAnalyticsTask): { label: string; tone: Tone } {
  if (t.approved === 'DISAPPROVED') return { label: '已驳回', tone: 'danger' }
  if (t.approved === 'NONE') return { label: '待审核', tone: 'warn' }
  if (t.deadline != null && t.deadline < Date.now()) return { label: '已截止', tone: 'muted' }
  return { label: '已上板', tone: 'ok' }
}

const deadlineDay = (ms: number | undefined) => (ms == null ? '不限' : utcDate(ms))

// --- 出题人 ------------------------------------------------------------------

/** 逐人一行，接口已经按累计被领取排好。这里只把 `successRate`（通过 / 提交）换成百分数，
 *  与原型「出题通过率」同一个定义 —— 后端 `_compute_publishers_rows` 就是这么算的。 */
const publisherTableRows = computed(() =>
  publishers.value.map((p) => ({
    id: p.publisherId,
    name: p.publisherName,
    taskCount: p.taskCount,
    participants: p.participantCount,
    // 接口给的是四位小数的比（`round(x, 4)`），表里读一位就够 —— 它是给人对照的量级，
    // 不是用来对账的精确值。
    avgParticipants: (p.avgParticipantsPerTask ?? 0).toFixed(1),
    successRate: pct(p.successRate),
  }))
)

// --- 参与者 ------------------------------------------------------------------

/** 报名与完成这一组数**只有** `/analytics/participants` 有：overview 的三张卡是
 *  「领取 / 提交 / 通过」，报名本身待审多少、被驳回多少它不说。 */
const participantSummary = computed<{
  rows: { label: string; value: number; icon: string; hint: string; tone?: Tone }[]
  total: number | null
}>(() => {
  const m: SpaceAnalyticsParticipantEntityMetrics | undefined = participants.value?.entityMetrics
  if (!m) return { rows: [], total: null }
  return {
    total: m.participantCount,
    rows: [
      { label: '报名主体', value: m.participantCount, icon: 'mdi-account-group-outline', hint: '个人或小队各算一个' },
      {
        label: '报名已通过',
        value: m.approvedParticipantCount,
        icon: 'mdi-account-check-outline',
        tone: 'ok' as Tone,
        hint: '审过才进得来',
      },
      {
        label: '报名待审',
        value: m.pendingParticipantCount,
        icon: 'mdi-account-clock-outline',
        tone: 'warn' as Tone,
        hint: '还在队列里',
      },
      {
        label: '报名已驳回',
        value: m.disapprovedParticipantCount,
        icon: 'mdi-account-cancel-outline',
        tone: 'danger' as Tone,
        hint: '被拒的报名',
      },
      { label: '提交主体', value: m.submittedParticipantCount, icon: 'mdi-tray-arrow-up', hint: '交过作业的主体' },
      {
        label: '成功主体',
        value: m.successfulParticipantCount,
        icon: 'mdi-trophy-outline',
        tone: 'ok' as Tone,
        hint: '判过且通过',
      },
    ],
  }
})

// --- 领了但没动的 --------------------------------------------------------------

/** 「领了没动」只有**题**这一层的条数（后端 14 天口径：有已通过的领取者、最近一次提交在
 *  14 天以前或从来没有）。逐人的那一层接口不返回 —— 列表照实空着，不按原型编名字。 */
const stalledCount = computed(() => alerts.value?.stalledTaskCount ?? 0)
</script>

<template>
  <div class="an">
    <div class="an__head">
      <div>
        <h1>数据看板</h1>
        <p>
          整块板的情况。这一屏只对所有者和管理员开放 —— 普通成员在「我的」里看得到的是自己出的那几道题。
          <template v-if="windowText">统计范围 {{ windowText }}。</template>
        </p>
      </div>
      <v-chip v-if="alertTotal" color="warning" variant="tonal" label>{{ alertTotal }} 件待处理</v-chip>
    </div>

    <v-empty-state
      v-if="failed"
      icon="mdi-lock-question"
      title="打不开这块看板"
      text="它只对这块板的所有者和管理员开放，而且要有这块板在里面。"
    />

    <template v-else>
      <v-tabs v-model="tab" density="comfortable" class="an__tabs">
        <v-tab value="overview">总览</v-tab>
        <v-tab value="alerts">待处理{{ alertTotal ? `（${alertTotal}）` : '' }}</v-tab>
        <v-tab value="tasks">题目</v-tab>
        <v-tab value="people">参与者</v-tab>
        <v-tab value="publishers">出题人</v-tab>
      </v-tabs>

      <!-- ===== 总览：先回答「现在怎么样」 ===== -->
      <div v-if="tab === 'overview'" class="an__pane">
        <div class="an__kpis">
          <MetricCard
            v-for="k in kpis"
            :key="k.label"
            :label="k.label"
            :value="k.value"
            :icon="k.icon"
            :hint="k.hint"
            :tone="k.tone"
          />
        </div>

        <div class="an__grid">
          <PanelCard class="an__span2" title="领取与提交" subtitle="最近 12 天 · 每天新增">
            <TrendChart v-if="hasTrend" :labels="trendLabels" :series="trendSeries" :height="220" />
            <v-empty-state
              v-else
              icon="mdi-chart-timeline-variant"
              title="最近 12 天没人领取或提交"
              text="这块板上的动静要落在这 12 天里，这里才有走势。"
            />
          </PanelCard>

          <PanelCard title="题目构成" subtitle="按状态">
            <SplitBar :segments="statusSegments" />
          </PanelCard>

          <PanelCard title="分类分布" subtitle="题目数">
            <BarList :rows="categoryRows" unit=" 道" empty="这块板还没有分类里的题" />
          </PanelCard>

          <PanelCard title="最热的题" subtitle="按领取主体数">
            <BarList :rows="hottest" unit=" 人" empty="这块板还没有题" />
          </PanelCard>

          <PanelCard title="出题最多的" subtitle="按累计被领取">
            <BarList :rows="publisherRows" unit=" 次" empty="还没有出题人" />
          </PanelCard>
        </div>
      </div>

      <!-- ===== 待处理：再回答「要我做点什么」 ===== -->
      <div v-else-if="tab === 'alerts'" class="an__pane">
        <div class="an__alerts">
          <div
            v-for="a in alertCards"
            :key="a.key"
            class="alert"
            :class="[`alert--${a.tone}`, { 'alert--zero': a.count === 0 }]"
          >
            <v-icon :icon="a.icon" size="20" />
            <div class="alert__body">
              <b>{{ a.title }}</b>
              <span>{{ a.detail }}</span>
            </div>
            <v-btn v-if="a.to" size="small" variant="tonal" :to="a.to">{{ a.cta }}</v-btn>
          </div>
        </div>

        <!-- 原型把这一格挂在「待处理」里（那一排 alert 之后），不是总览 —— 照原型摆。 -->
        <PanelCard title="领了但没动的" :subtitle="`${stalledCount} 道题上有已通过的领取者、两周没提交`">
          <div class="stalled">
            <b class="stalled__num">{{ stalledCount }}</b>
            <span class="stalled__unit">道题</span>
          </div>
          <p class="an__note">
            口径是<strong>两周</strong>：有已通过的领取者，而最近一次提交在 14 天以前（或者从来没交过）。
            原型那一格写「超过五天」，真版按接口的 14 天说，这个差是有意记在案的。 能给的也只有<strong>题</strong>这一层
            —— 谁在哪道题上没动，逐人的名单接口不返回 （真平台上只有一份带审计的导出 CSV
            里有），所以这一格只有数、不列名字：少画，不编。
          </p>
        </PanelCard>

        <PanelCard
          v-if="noSubmitTasks.length"
          title="领了题但没人交的"
          subtitle="有人领、至今零提交 —— 上面「两周没动静」那一格是从这些里按 14 天的线挑出来的"
        >
          <ul class="mini">
            <li v-for="t in noSubmitTasks" :key="t.taskId">
              <router-link :to="taskTo(t.taskId)" class="mini__title">{{ t.taskName }}</router-link>
              <v-spacer />
              <span class="mini__meta">{{ t.publisher.name }} 出题 · {{ t.participantCount }} 人领</span>
            </li>
          </ul>
        </PanelCard>

        <PanelCard v-if="coldTasks.length" title="上板后没人领的" subtitle="发出去了还是零领取，值得回看题目本身">
          <ul class="mini">
            <li v-for="t in coldTasks" :key="t.taskId">
              <router-link :to="taskTo(t.taskId)" class="mini__title">{{ t.taskName }}</router-link>
              <v-spacer />
              <span class="mini__meta">{{ t.publisher.name }} 出题</span>
            </li>
          </ul>
        </PanelCard>
      </div>

      <!-- ===== 题目：逐题一行，接口全给 ===== -->
      <div v-else-if="tab === 'tasks'" class="an__pane">
        <PanelCard title="全部题目" :subtitle="`${taskRows.length} 道 · 按领取人数排`">
          <v-table density="comfortable" class="an__table">
            <thead>
              <tr>
                <th>题目</th>
                <th>出题人</th>
                <th class="num">领取</th>
                <th class="num">提交</th>
                <th class="num">通过率</th>
                <th>状态</th>
                <th>截止</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in pagedTaskRows" :key="t.taskId">
                <td>
                  <router-link :to="taskTo(t.taskId)" class="an__link">{{ t.taskName }}</router-link>
                  <div class="an__sub">{{ t.category.name }}</div>
                </td>
                <td>{{ t.publisher.name }}</td>
                <td class="num">{{ t.participantCount }}</td>
                <td class="num">{{ t.submittedParticipantCount }}</td>
                <td class="num">{{ pct(t.successRate) }}</td>
                <td>
                  <v-chip size="x-small" label variant="tonal" :class="`tone-${taskState(t).tone}`">
                    {{ taskState(t).label }}
                  </v-chip>
                </td>
                <td class="an__sub">{{ deadlineDay(t.deadline) }}</td>
              </tr>
            </tbody>
          </v-table>

          <v-empty-state
            v-if="!taskRows.length"
            icon="mdi-file-document-outline"
            title="这块板还没有题"
            text="窗口里一道题都没有 —— 题目可能都在窗口之外，或者还没过审。"
          />

          <PageBar :page="taskPage" :page-size="PAGE_SIZE" :total="taskRows.length" @update:page="taskPage = $event" />

          <p class="an__note">
            四项都是接口给的：领取＝`participantCount`（领取主体数），提交＝`submittedParticipantCount`，
            通过率＝`successRate`（<strong>通过 / 提交</strong>，分母是提交不是领取）。状态由 `approved`
            加上截止时间推出来：「已截止」= 过审了但过了截止日。
          </p>
        </PanelCard>
      </div>

      <!-- ===== 参与者：接口只回聚合，逐人那一层不画 ===== -->
      <div v-else-if="tab === 'people'" class="an__pane">
        <PanelCard
          title="参与者"
          :subtitle="
            participantSummary.total === null
              ? '逐人的那一层接口不返回'
              : `${participantSummary.total} 个主体领过题 · 逐人的那一层接口不返回，排不了名`
          "
        >
          <div v-if="participantSummary.rows.length" class="an__kpis">
            <MetricCard
              v-for="k in participantSummary.rows"
              :key="k.label"
              :label="k.label"
              :value="k.value"
              :icon="k.icon"
              :hint="k.hint"
              :tone="k.tone"
            />
          </div>

          <p class="an__note">
            原型这一格是<strong>逐人</strong>一行：谁领了几道、还有几道在做、活跃还是不活跃。真版没有这一层 ——
            `/analytics/participants` 只回聚合的报名与完成情况，<strong>不返回成员名单</strong>，
            所以「领取数」「在做的」「活跃 / 不活跃」这三列没有来源。这一格只画接口真有的那一层
            （报名与完成的主体数），不照原型编一张人表。逐人的明细真平台上只有一份带审计日志的 CSV
            导出，不是这一屏该去拉的东西。
          </p>
        </PanelCard>
      </div>

      <!-- ===== 出题人：逐人一行，接口全给 ===== -->
      <div v-else class="an__pane">
        <PanelCard title="出题人" subtitle="开放发题之后，这一格是看「谁在认真出题」的地方">
          <v-table density="comfortable" class="an__table">
            <thead>
              <tr>
                <th>出题人</th>
                <th class="num">题目数</th>
                <th class="num">累计被领取</th>
                <th class="num">平均每道被领</th>
                <th class="num">出题通过率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in publisherTableRows" :key="p.id">
                <td>{{ p.name }}</td>
                <td class="num">{{ p.taskCount }}</td>
                <td class="num">{{ p.participants }}</td>
                <td class="num">{{ p.avgParticipants }}</td>
                <td class="num">{{ p.successRate }}</td>
              </tr>
            </tbody>
          </v-table>

          <v-empty-state
            v-if="!publisherTableRows.length"
            icon="mdi-account-edit-outline"
            title="窗口里还没有出题人"
            text="题目按创建时间落在窗口里 —— 窗口外的出题人不在这里。"
          />

          <p class="an__note">
            「平均每道被领」与「出题通过率」都是接口算好的（`avgParticipantsPerTask`、 `successRate` = 通过 /
            提交）。通过率高不一定是好事（可能题太水），低也不一定是坏事 （可能题够硬）——
            列在这里是给人<strong>对照</strong>用的，不是打分。
          </p>
        </PanelCard>
      </div>

      <p class="an__foot">
        这一屏的数字按<strong>主体</strong>算（个人或一支小队各算一个），窗口是接口自己给的那一段（默认最近 180
        天，题目按创建时间落在里面）。
        「题目」与「出题人」两格逐行都是接口给的；「参与者」那一格<strong>只有聚合</strong>：
        「谁领了几道、还在不在做」接口不返回，那几列就不画。
        「领了没动」也只有<strong>题</strong>这一层的条数，逐人的名单接口同样不返回 —— 少画，不编。
        要逐人翻明细，走老树那九页（一页没删，仍在老地址上服务）：
        <router-link :to="{ name: 'SpacesDetailAnalytics', params: { spaceId } }" class="an__link">
          打开老版九页分析
        </router-link>
      </p>
    </template>
  </div>
</template>

<style scoped lang="scss">
.an__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.an__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.an__head p {
  max-width: 720px;
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.83rem;
  line-height: 1.7;
}

.an__tabs {
  margin-bottom: 18px;
}

.an__pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.an__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: 12px;
}

.an__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .an__grid {
    grid-template-columns: 1fr;
  }
}

.an__span2 {
  grid-column: span 2;
}

@media (max-width: 900px) {
  .an__span2 {
    grid-column: span 1;
  }
}

.an__alerts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.alert {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 12px;
}

.alert--zero {
  opacity: 0.5;
}

.alert--warn {
  color: rgb(var(--v-theme-warning));
}

.alert--danger {
  color: rgb(var(--v-theme-error));
}

.alert--muted {
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.alert__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  color: rgb(var(--v-theme-on-surface));
}

.alert__body b {
  font-size: 0.86rem;
}

.alert__body span {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.75rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini {
  padding: 0;
  margin: 0;
  list-style: none;
}

.mini li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 0;
  font-size: 0.83rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.mini li:first-child {
  border-top: none;
}

.mini__title {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.8);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini__title:hover {
  text-decoration: underline;
}

.mini__meta {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.an__link {
  color: rgb(var(--v-theme-primary));
  text-decoration: none;
}

.an__link:hover {
  text-decoration: underline;
}

/* 三张表共用一套书写法：表头灰、正文小一号、数字列右对齐等宽。板里 `Members.vue`
   也是这么写的，两张表在这一屏里不该长得不一样。 */
.an__table {
  background: transparent;
}

.an__table :deep(th) {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
  font-weight: 500;
}

.an__table :deep(td) {
  font-size: 0.83rem;
}

.an__table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* 题目名底下那一行小字（分类）。 */
.an__sub {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
}

/* 表底下那一段口径。 */
.an__note {
  margin: 14px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.78rem;
  line-height: 1.7;
}

.tone-ok {
  color: rgb(var(--v-theme-success));
}

.tone-warn {
  color: rgb(var(--v-theme-warning));
}

.tone-danger {
  color: rgb(var(--v-theme-error));
}

.tone-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
}

/* 「领了但没动的」那一格：一个大数 + 口径。数在这里只说明量级，口径才是这一格的内容。 */
.stalled {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.stalled__num {
  font-size: 1.9rem;
  font-weight: 650;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.stalled__unit {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.85rem;
}

.an__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
  line-height: 1.8;
}
</style>
