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
// 老树那九页一页没删，仍在 `/spaces/:id/analytics/*` 上原样服务：要逐题、逐人、逐
// 出题人（以及课程的学习那一格）翻明细时，走页脚那条链。
import type {
  AnalyticsTimeSeriesPoint,
  SpaceAnalyticsAlerts,
  SpaceAnalyticsOverview,
  SpaceAnalyticsPublisherMetrics,
  SpaceAnalyticsTask,
} from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import BarList from '../components/BarList.vue'
import MetricCard from '../components/MetricCard.vue'
import PanelCard from '../components/PanelCard.vue'
import SplitBar from '../components/SplitBar.vue'
import TrendChart from '../components/TrendChart.vue'

import { SpacesApi } from '@/network/api/spaces'

type Tone = 'ok' | 'warn' | 'danger' | 'muted'

const DAY = 86_400_000
/** 走势画最近 12 天，和原型同一段。后端按天分桶，桶的边界是 UTC 零点。 */
const TREND_DAYS = 12

const route = useRoute()
const spaceId = computed(() => Number(route.params.spaceId))

const tab = ref<'overview' | 'alerts'>('overview')

const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
/** 逐题一行。接口一次给全（不分页），所以「三天内截止」「无人领取」是在全量上挑。 */
const taskRows = ref<SpaceAnalyticsTask[]>([])
const publishers = ref<SpaceAnalyticsPublisherMetrics[]>([])

const loading = ref(true)
const failed = ref(false)

async function load() {
  const id = spaceId.value
  if (!Number.isFinite(id) || id <= 0) return
  loading.value = true
  failed.value = false
  try {
    const [overviewRes, alertsRes, tasksRes, publishersRes] = await Promise.all([
      SpacesApi.getAnalyticsOverview(id, { groupBy: 'day' }),
      SpacesApi.getAnalyticsAlerts(id),
      // `sortBy` 必须显式给：这一条路由的默认值是 `publishedAt`，而后端认的排序字段
      // 里没有它（`TASK_SORT_FIELDS`），不传会直接 400 —— 接口不退回默认排序。
      SpacesApi.getAnalyticsTasks(id, { sortBy: 'participantCount', sortOrder: 'desc' }),
      // 出题人排行按「累计被领取」排，与原型那一格同义。
      SpacesApi.getAnalyticsPublishers(id, { sortBy: 'participantCount', sortOrder: 'desc' }),
    ])
    overview.value = overviewRes.data
    alerts.value = alertsRes.data
    taskRows.value = tasksRes.data.tasks ?? []
    publishers.value = publishersRes.data.publishers ?? []
  } catch {
    // 读不到（没权限、空间没了、接口改了）就说一句，不留一屏看着像「这块板是空的」。
    overview.value = null
    alerts.value = null
    taskRows.value = []
    publishers.value = []
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
      <div v-else class="an__pane">
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

      <p class="an__foot">
        这一屏的数字按<strong>主体</strong>算（个人或一支小队各算一个），窗口是接口自己给的那一段（默认最近 180
        天，题目按创建时间落在里面）。
        「领了没动」只有条数没有名单：接口给的是<strong>题</strong>上两周没有提交的条数，逐人的「谁领了几天没动」它不返回
        —— 少画一格，不编。 要逐题、逐人、逐出题人翻明细，走老树那九页（一页没删，仍在老地址上服务）：
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

.an__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
  line-height: 1.8;
}
</style>
