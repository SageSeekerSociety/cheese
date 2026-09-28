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
// 3. **领了没动**。接口给两层：`/analytics/alerts` 的 `stalledTaskCount` 是**题**上两周
//    没有提交的条数（后端 14 天口径），`/analytics/people` 的 `stalled` 是**逐条领取**：
//    谁在哪道题上领了两周没动。那一格列的是后者的名单。
//
// 五个 tab 里的「题目 / 参与者 / 出题人」三格照原型排，逐人的那一层走上面那条
// `/analytics/people`：
// - 题目：`/analytics/tasks` 逐题一行，全在（`participantLimit` 是名额上限，没设的是 null）。
// - 出题人：`/analytics/publishers` 逐人一行，含 `successRate`（= 通过 / 提交）与
//   `avgParticipantsPerTask`，两个数接口自己算好给。
// - 参与者：`/analytics/participants` 回聚合（报名与完成的主体数、分布、走势），
//   `/analytics/people` 回逐人一行（领了几道、走到哪一步）。两格说的是两件事，都画。
//   逐人那一层的状态由**提交与评审**算出来（判过且通过 = 通过；有提交没判 = 已交），
//   不看 `task_membership.completion_status` —— 那一列后端没有请求路径会推进它。
//
// 老树那九页一页没删，仍在 `/spaces/:id/analytics/*` 上原样服务：要逐题、逐人、逐
// 出题人（以及课程的学习那一格）翻明细时，走页脚那条链。
import type {
  AnalyticsTimeSeriesPoint,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsParticipantEntityMetrics,
  SpaceAnalyticsParticipants,
  SpaceAnalyticsPeople,
  SpaceAnalyticsPerson,
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
/** 参与者那一格的聚合：报名与完成的主体数、分布、走势。 */
const participants = ref<SpaceAnalyticsParticipants | null>(null)
/** 逐人那一格 + 「领了没动」的名单。与上面五条**分开取**：那五条是聚合，这一条是名单，
 *  它读不到时只这一格说一句，整屏不该跟着塌。 */
const people = ref<SpaceAnalyticsPeople | null>(null)
const peopleFailed = ref(false)

const loading = ref(true)
const failed = ref(false)

async function load() {
  const id = spaceId.value
  if (!Number.isFinite(id) || id <= 0) return
  loading.value = true
  failed.value = false
  peopleFailed.value = false
  try {
    const [overviewRes, alertsRes, tasksRes, publishersRes, participantsRes, peopleRes] = await Promise.all([
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
      // 逐人那一格自己吞掉失败：它挂了只把这一格标成「读不到」，其余五条照画。
      SpacesApi.getAnalyticsPeople(id).catch(() => null),
    ])
    overview.value = overviewRes.data
    alerts.value = alertsRes.data
    taskRows.value = tasksRes.data.tasks ?? []
    publishers.value = publishersRes.data.publishers ?? []
    participants.value = participantsRes.data
    if (peopleRes) {
      people.value = peopleRes.data
    } else {
      people.value = null
      peopleFailed.value = true
    }
  } catch {
    // 读不到（没权限、空间没了、接口改了）就说一句，不留一屏看着像「这块板是空的」。
    overview.value = null
    alerts.value = null
    taskRows.value = []
    publishers.value = []
    participants.value = null
    people.value = null
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
      // 本周与前一周比的是**新增**（走势按天的桶相加），不是这个累计值本身 —— 卡片上的
      // 数是累计，副行说的是最近两周的增量，两个口径都如实写出来。
      tone: twoWeeks.value ? (twoWeeks.value.last >= twoWeeks.value.prev ? 'ok' : 'warn') : undefined,
      hint: twoWeeks.value
        ? `近 7 天新增 ${twoWeeks.value.last} · 前 7 天 ${twoWeeks.value.prev}`
        : `已通过 ${m.approvedParticipantCount}`,
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

/** 走势的两种读法：默认累计（原型画的就是两条累计线），切到「每天」看当天的量。
 *  两种画法用**同一批桶**：累计只是把每天的数往前加，不是另取一份数据。 */
const trendMode = ref<'cumulative' | 'daily'>('cumulative')

const cumulative = (values: number[]) => {
  let running = 0
  return values.map((v) => (running += v))
}

const trendSeries = computed(() => {
  const trends = overview.value?.trends
  const daily = [
    { name: '每天领取', values: toDaily(trends?.participantsJoined ?? []) },
    { name: '每天提交', values: toDaily(trends?.submissionsCreated ?? []) },
  ]
  if (trendMode.value === 'daily') return daily
  return daily.map((s) => ({ name: s.name.replace('每天', '累计'), values: cumulative(s.values) }))
})

/** 最近 7 天与前 7 天的**新增领取主体**：接口已经按 UTC 日历日分好桶，这里只把落在
 *  那两段里的桶相加 —— 不另算、也不按下标挤。桶多一天少一天都不会算错。 */
const twoWeeks = computed(() => {
  const points = overview.value?.trends.participantsJoined ?? []
  if (!points.length) return null
  const now = new Date()
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  const sum = (from: number, to: number) =>
    points.filter((p) => p.bucket >= from && p.bucket < to).reduce((n, p) => n + p.count, 0)
  return { last: sum(today - 6 * DAY, today + DAY), prev: sum(today - 13 * DAY, today - 6 * DAY) }
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
}>(() => {
  const m: SpaceAnalyticsParticipantEntityMetrics | undefined = participants.value?.entityMetrics
  if (!m) return { rows: [] }
  return {
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

// --- 逐人那一格 ----------------------------------------------------------------

// 「领了没动」有两个粒度，别混：`alerts.stalledTaskCount` 是**题**上两周没提交的条数
// （进的是「待处理」那排 alert），下面 `stalledClaims` 是**逐条领取**（谁在哪道题上）。
// 一个人在两道题上各领了两周没动时，那边算两道题、这边算两条。

/** 接口按领取数降序给（同数按个人在前），页面照它画 —— 与逐题、出题人两张表同一套信任。 */
const peopleRows = computed(() => people.value?.people ?? [])

/** 「领了没动」的**逐条领取**：谁、在哪道题上、什么时候领的。接口按领取时间升序给，
 *  领得最久的排在最前 —— 这一格问的是「先问谁」。 */
const stalledClaims = computed(() => people.value?.stalled ?? [])

/** 还在做 = 还在做 + 交了没判。原型那一列（`p.active`）就是这两类相加；已判过的
 *  （通过或没过）都不再算「在做」。 */
const activeOf = (p: SpaceAnalyticsPerson) => p.inProgress + p.submitted

/** 「N 天前领的」。领取到今天不满一天也算一天 —— 这一格是提醒谁该被问一句，不是账目。 */
const daysAgoText = (ms: number) => {
  const days = Math.max(0, Math.floor((Date.now() - ms) / DAY))
  return days === 0 ? '今天领的' : `${days} 天前领的`
}

const peopleSubtitle = computed(() => {
  if (peopleFailed.value) return '逐人的那一层没读出来'
  if (!people.value) return ''
  return `${peopleRows.value.length} 个主体领过题 · 按领取数排`
})

const stalledSubtitle = computed(() =>
  peopleFailed.value ? '逐人的名单没读出来' : `${stalledClaims.value.length} 条领取两周没动 · 按领取时间排`
)

const stalledNote = computed(() => {
  if (peopleFailed.value) {
    return '逐人的名单没读出来 —— 这一格只对这块板的所有者和管理员开放。读不到就只说明这一句，不照原型编名字。'
  }
  const standard =
    '口径是两周：领取超过 14 天、而且一版提交都没有。交过一版的（哪怕还没判）不在这里，才领了三天的也不算。'
  return stalledClaims.value.length ? standard : `这两周没有人领了题一直没动。${standard}`
})
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
          <PanelCard
            class="an__span2"
            title="领取与提交"
            :subtitle="`最近 12 天 · ${trendMode === 'cumulative' ? '累计' : '每天新增'}`"
          >
            <template #actions>
              <v-btn-toggle v-model="trendMode" density="comfortable" variant="outlined" divided mandatory>
                <v-btn value="cumulative" size="small">累计</v-btn>
                <v-btn value="daily" size="small">每天</v-btn>
              </v-btn-toggle>
            </template>
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
        <PanelCard title="领了但没动的" :subtitle="stalledSubtitle">
          <ul v-if="stalledClaims.length" class="mini">
            <li v-for="c in stalledClaims" :key="`${c.taskId}-${c.isTeam ? 'team' : 'user'}-${c.userId}`">
              <span class="mini__who">{{ c.name }}</span>
              <router-link :to="taskTo(c.taskId)" class="mini__title">{{ c.taskTitle }}</router-link>
              <v-spacer />
              <span class="mini__meta">{{ daysAgoText(c.claimedAt) }}</span>
            </li>
          </ul>
          <p class="an__note">{{ stalledNote }}</p>
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
              <!-- 截止日是响应里就有的（`deadline`），没设的写「不限」—— 零领取的题回看时，
                   先看还有多久。 -->
              <span class="mini__meta">{{ t.publisher.name }} 出题 · {{ deadlineDay(t.deadline) }}</span>
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
                <td class="num">
                  {{ t.participantCount
                  }}<span v-if="t.participantLimit != null" class="an__cap"> / {{ t.participantLimit }}</span>
                </td>
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
            五项都是接口给的：领取＝`participantCount`（领取主体数），提交＝`submittedParticipantCount`，
            通过率＝`successRate`（<strong>通过 / 提交</strong>，分母是提交不是领取），领取那一列斜杠后面
            是这道题的名额上限（`participantLimit`）—— 没设上限的题只写一个数，不画「/ 上限」。 状态由 `approved`
            加上截止时间推出来：「已截止」= 过审了但过了截止日。
          </p>
        </PanelCard>
      </div>

      <!-- ===== 参与者：逐人一行（`/analytics/people`）+ 报名与完成的聚合 ===== -->
      <div v-else-if="tab === 'people'" class="an__pane">
        <PanelCard title="参与者" :subtitle="peopleSubtitle">
          <v-table v-if="peopleRows.length" density="comfortable" class="an__table">
            <thead>
              <tr>
                <th>成员</th>
                <th class="num">领取</th>
                <th class="num">通过</th>
                <th class="num">在做的</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in peopleRows" :key="`${p.isTeam ? 'team' : 'user'}-${p.userId}`">
                <td>
                  {{ p.name }}
                  <div v-if="p.isTeam" class="an__sub">小队</div>
                </td>
                <td class="num">{{ p.claims }}</td>
                <td class="num">{{ p.passed }}</td>
                <td class="num">{{ activeOf(p) }}</td>
                <td>
                  <v-chip size="x-small" label variant="tonal" :class="activeOf(p) ? 'tone-warn' : 'tone-ok'">
                    {{ activeOf(p) ? `${activeOf(p)} 道在做` : '都收尾了' }}
                  </v-chip>
                </td>
              </tr>
            </tbody>
          </v-table>

          <p v-if="peopleFailed" class="an__note">
            逐人的名单没读出来 —— 这一格只对这块板的所有者和管理员开放。读不到就只说这一句， 不照原型编一张人表。
          </p>
          <v-empty-state
            v-else-if="people && !peopleRows.length"
            icon="mdi-account-off-outline"
            title="还没有人领过题"
            text="窗口里这块板上的题一条领取都没有。"
          />

          <p v-else class="an__note">
            逐人这一行来自 <code>/analytics/people</code>：领取＝这位在这块板上领了几道题，通过＝其中判过且通过的，
            「在做的」＝还在做或交了没判的（判过的不再算）。状态那枚 chip 就是这一列的说法 ——
            一个人只剩判完的题时写「都收尾了」。小队按队一行。
          </p>

          <div v-if="participantSummary.rows.length" class="an__subhead">报名与完成（只按主体汇总）</div>
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
        「题目」「参与者」「出题人」三格逐行都是接口给的；参与者那张人表里，一个人的状态由
        <strong>提交与评审</strong>算出来（判过且通过＝通过，交了没判＝在做），不看报名表上那份完成状态。
        「领了没动」是<strong>逐条领取</strong>的名单，口径 14 天。
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

.mini__who {
  font-weight: 600;
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

/* 领取那一列里「/ 上限」那一截：上限是次要信息，压淡一点。 */
.an__cap {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
}

/* 一张面板里换一组东西时的小标题（参与者：逐人表 → 报名与完成的汇总）。 */
.an__subhead {
  margin: 18px 0 10px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
  font-weight: 600;
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

.an__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
  line-height: 1.8;
}
</style>
