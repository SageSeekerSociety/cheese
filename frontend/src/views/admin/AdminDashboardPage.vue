<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { StatsKind } from '@/api'
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminBreakTable from '@/components/admin/AdminBreakTable.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminLiveSpine from '@/components/admin/AdminLiveSpine.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'
import { relTime } from '@/lib/relTime'
import { fmtCost, fmtDelta, fmtMs, fmtNum, fmtSI } from '@/lib/usageFormat'
import { useFeedbackStore, WINDOWED_KINDS } from '@/stores/feedback'

// 管理后台的看板（§4.2）。**它读的是整个平台，不只是反馈。**
//
// 分类是这一页的骨架（需求方原话：「看板我觉得不是专门为反馈打造的？其它的也应该算
// 进去，比如 token 消费之类的，然后表也不要太多……不然人的注意力比较稀疏」）。各类
// 各占一屏，切到哪一类才拉哪一类 —— 服务端一分类一条接口（`/admin/stats/{…}`），而
// 其中用量那块读的是全平台增长最快的表（`resource_usage` 每调一次 `/v1/messages` 长
// 一行），没有理由让「看一眼反馈」把用量也读一遍。
//
// 一页里并列八张图是**没人读**的页面：每一块都有自己的尺度和自己的读者，摆在一起只会
// 让注意力平均分配，而那恰好等于没有重点。所以「表不要太多」不是删表，是**分屏**。
//
// 数字口径有两处是刻意的：
//
//   * **存量与窗口分开**。`counts` 是全量（现在一共多少条），`series` 是窗口内的
//     （这七天怎么变的）—— 页面上「现在是多少」和「这七天怎么走的」是两个问题，
//     合成一个数就会有一个是错的。窗口（7/30/90 天）由 store 的 `statsDays` 记，
//     缓存键就是窗口：切窗口把已加载的窗口类全部重拉（`setStatsDays`），不留多窗口
//     副本 —— 否则导轨短值会混窗口（用量卡显示 30 天的、反馈卡显示 7 天的）。
//   * **未定价的 token 单独算**。订阅按月计费，那些行上的 `cost_usd = 0.0` 意思是
//     「没有单价」而不是「免费」；把它们加进总额，会在几百万 token 上印一个
//     `$0.0000`，读起来像「这个月没花钱」。
//
// 墙上每个数字要么写得出点下去去哪，要么说得出**为什么不能点**（§6.3 那张准入表）。
// 机器那四个数是例外，而且是**说得出理由的例外**：平台里没有一页列设备，它们点不进
// 去，所以那一块只报数、不装成链接（口径是存量，见 `machines`）。「机器连败」那一栏
// 同理 —— 它曾经是跳回 `/admin` 的自链，假 affordance 比不点更糟，所以去掉了。
//
// 口径注脚（「这个数是怎么算的」）一律收进 `AdminNoteTip`（标题旁的 info 图标 +
// tooltip），不再以 12.5px 灰字散行叠在块底 —— 一屏七八条注脚等于没有重点。防误读级
// 的常驻注每屏至多一条（平台的 machines.note、性能的 perf.note）。
defineOptions({ name: 'AdminDashboardPage' })

const store = useFeedbackStore()
const { t } = useI18n()
const router = useRouter()

/** 分类的顺序就是这里的顺序。三类**一一对应服务端那三条接口**，不多不少：把「账号」
 *  和「机器」拆成两个分类的话，它们会各拉一次同一条 `/admin/stats/platform`。 */
const KINDS: StatsKind[] = ['pipeline', 'product', 'feedback', 'usage', 'platform', 'performance', 'integrations']

/** 每个分类的名字。**写成一张键名字面量的表**，不在模板里拼
 *  `feedback.dashboard.tab.${kind}` —— 拼出来的键在源码里没有一处字面量出现，
 *  `catalog.spec.ts` 的「这个键没有任何文件引用」那条闸门就会把这三个键判成没人用的
 *  死词条（它扫的是源码文本，不是运行时的调用）。拼字符串在这里省下的是一行，代价是
 *  每次跑门禁都要重新解释一遍「这三个键其实是活的」。 */
const TAB_KEY: Record<StatsKind, string> = {
  pipeline: 'feedback.dashboard.tab.pipeline',
  product: 'feedback.dashboard.tab.product',
  feedback: 'feedback.dashboard.tab.feedback',
  usage: 'feedback.dashboard.tab.usage',
  platform: 'feedback.dashboard.tab.platform',
  performance: 'feedback.dashboard.tab.performance',
  integrations: 'feedback.dashboard.tab.integrations',
}

/** 队列的地址。写**地址**不写路由名：规格 §11 第 9 条钉的是地址。 */
const QUEUE = '/admin/queue'
const queue = (query: Record<string, string> = {}): RouteLocationRaw => ({ path: QUEUE, query })

/** 迷你列表最多画几行。和 `AdminNumberList` 的 `SHOWN` 是同一个数。 */
const SHOWN = 10

type PulseTone = 'ink' | 'ok' | 'warn' | 'danger'

/** 分类导轨上一颗迷你摘要卡的内容。 */
interface PulseRow {
  key: StatsKind
  value: string
  hint: string
  tone: PulseTone
}

/** 顶上那条分类导轨 —— 这一页的**第一眼**。
 *
 *  分类控件把四块藏在四个抽屉里，于是「反馈在催、用量在涨」这件事要么点三下才看见，
 *  要么根本看不见。这一条把每一块的那**一个**数摆在一起：读的人先知道「现在哪块
 *  需要我」，再决定进哪一块。
 *
 *  每块只取一个数（不是四个）：一行摆四个指标 × 四块 = 十六个数，那就不是摘要而是
 *  另一张表。取哪一个，判据是「这一块现在最该被看见的那件事」：
 *
 *   * 反馈 → **待分诊**（有事等着人动）；有急件时换成急件数并染警示色。
 *   * 用量 → 窗口内的 **token**（量级，比钱稳 —— 未定价的那部分没有钱数）。
 *   * 平台 → **健康度**（`healthy` / `degraded`，这一刻的）。
 *   * 性能 → **最慢那条的 p95**（「哪条慢」是这一块唯一要回答的问题）。
 *
 *  点一块就切到那一类 —— 它是导航，不是卡片（`to` 语义上的链接由下面的 KPI 卡承担）。
 *
 *  **「没读到」和「是零」必须分开**：那一类还是 null 时短值画「—」，不是「等你 0」
 *  （`?? 0` 曾经让没加载的类显示一个假 0 —— 全站纪律：没读到画破折号，不画 0）。 */
const pulse = computed<PulseRow[]>(() => {
  const row = (key: StatsKind, value: string, hint: string, tone: PulseTone): PulseRow =>
    store.stats[key] === null ? { key, value: '—', hint, tone: 'ink' } : { key, value, hint, tone }
  return [
    row(
      'pipeline',
      // 「等你」是这一块唯一要回答的问题，而且它**不在**下面的 KPI 第一张
      // （第一张是「还在走」）。
      t('feedback.dashboard.pipeline.kpi.needsYouShort', {
        n:
          (pipeline.value?.needs_you.reviewer_pending ?? 0) +
          (pipeline.value?.needs_you.open_tasks ?? 0) +
          (pipeline.value?.needs_you.awaiting_answer ?? 0),
      }),
      t('feedback.dashboard.pipeline.needs.title'),
      'ink'
    ),
    row(
      'product',
      fmtNum(product.value?.north_star.total ?? 0),
      t('feedback.dashboard.product.northStar', { d: store.statsDays }),
      'ink'
    ),
    row(
      'feedback',
      urgentOpen.value > 0
        ? t('feedback.dashboard.kpi.urgentShort', { n: urgentOpen.value })
        : t('feedback.dashboard.kpi.untriagedShort', {
            n: feedback.value?.total.unassigned ?? 0,
          }),
      urgentOpen.value > 0 ? t('feedback.dashboard.kpi.urgent') : t('feedback.dashboard.kpi.untriaged'),
      urgentOpen.value > 0 ? 'warn' : 'ink'
    ),
    row('usage', shortTokens(usage.value?.totals.tokens), t('feedback.dashboard.usage.tokens'), 'ink'),
    row(
      'platform',
      platform.value?.health?.overall === 'healthy'
        ? t('feedback.dashboard.health.healthy')
        : platform.value?.health?.overall === 'degraded'
          ? t('feedback.dashboard.health.degraded')
          : '—',
      t('feedback.dashboard.health.title'),
      platform.value?.health?.overall === 'healthy'
        ? 'ok'
        : platform.value?.health?.overall === 'degraded'
          ? 'danger'
          : 'ink'
    ),
    row('performance', slowestP95.value, t('feedback.dashboard.perf.slowest'), 'ink'),
    row(
      'integrations',
      t('feedback.dashboard.integrations.delivery.deadShort', {
        n: integrations.value?.delivery.dead_letters ?? 0,
      }),
      t('feedback.dashboard.integrations.delivery.title'),
      (integrations.value?.delivery.dead_letters ?? 0) > 0 ? 'warn' : 'ink'
    ),
  ]
})

/** 一键一格，给上面那条导轨按 `StatsKind` 取短值用。`pulse` 仍是数组（渲染顺序），
 *  这里只是同一批数据的按名索引。 */
const pulseByKey = computed<Record<string, PulseRow>>(() => {
  const out: Record<string, PulseRow> = {}
  for (const row of pulse.value) out[row.key] = row
  return out
})

/** 20 万 token 这种短写 —— 导轨上摆 `204,900` 是把下面 KPI 的同一个数再念一遍。
 *  走 `fmtSI` 而不是手写阶梯：手写的那份止步于 M，`1e12` 会被打成 `1000000.0M`。 */
const shortTokens = (n: number | null | undefined): string => (n === null || n === undefined ? '—' : fmtSI(n))

/** 最慢那条路由的 p95 —— 「哪条慢」是性能那一块唯一要回答的问题。取**最大值**
 *  而不是第一个元素：fixture 与旧响应都给出过未排序的数组。 */
const slowestP95 = computed(() => {
  const rows = perf.value?.routes ?? []
  const best = rows.reduce<number | null>((acc, row) => {
    if (row.p95 === null || row.p95 === undefined) return acc
    return acc === null || row.p95 > acc ? row.p95 : acc
  }, null)
  return best === null ? '—' : fmtMs(best)
})

const kind = computed(() => store.statsKind)
const feedback = computed(() => store.stats.feedback)
const usage = computed(() => store.stats.usage)
const platform = computed(() => store.stats.platform)

/** 当前这一类拿到了没有。`null` = 还没拉到（第一次进来）或这一趟没成。 */
const current = computed(() => store.stats[kind.value])
/** 整页失败 = 当前那一类什么都没拿到，而 store 里有一句服务端的原话。 */
const failed = computed(() => !store.statsLoading && current.value === null && store.error !== null)

/** 数字串。拿不到来源（`null`）时给空串，卡片自己画成 `—`；给 0 的话「没读到」和
 *  「读出来确实是零」在屏幕上就分不开。 */
const num = (v: number | null | undefined): string => (v === null || v === undefined ? '' : fmtNum(v))

/** 切分类：先写状态（控件立刻跟上），再拉这一类。已经拉过的那一类**不重拉** ——
 *  切回来看到的是刚才那份，而「重新拉一次」是刷新按钮或轮询的事。 */
function selectKind(next: StatsKind) {
  if (next === store.statsKind) return
  store.statsKind = next
  if (store.stats[next] === null) void store.loadStats(next, store.statsDays)
}

/** KPI 卡一行的完整形状（模板按这个形状传参，省的每行猜有哪些键）。 */
interface KpiRow {
  key: string
  label: string
  value: string
  loading: boolean
  to?: RouteLocationRaw
  delta?: string
  deltaTitle?: string
  spark?: (number | null)[]
  note?: string
}

/** delta 与其口径句（挂 title 的「上一周期（再前 {d} 天）：{v}」）。
 *  `prev` 缺字段（旧后端）→ 两个都不给，卡上不出现 delta；`prev = 0` → `fmtDelta`
 *  给空串（不画「+∞%」这种鬼话）。`prevText` 是已格式化的 prev 全值（fmtNum / fmtCost）。 */
function deltaOf(cur: number | null | undefined, prev: number | null | undefined, prevText: string) {
  if (prev === null || prev === undefined) return { delta: undefined, deltaTitle: undefined }
  return {
    delta: fmtDelta(cur, prev),
    deltaTitle: t('feedback.dashboard.kpi.vsPrev', { d: store.statsDays, v: prevText }),
  }
}

/* ---- 反馈那一块 ---- */

/** 反馈的 KPI。**四张都走看板接口这一条**（`/admin/stats/feedback` 的 `total`），
 *  不再有前两张走列表接口那种混搭。
 *
 *  改的原因是两类错，都出在「同一个数有两个来源」上：
 *
 *   * 那两个计数（`unassigned` / `in_progress`）曾在**列表接口**那一趟里也回，而列表
 *     那一趟失败时它的初值是全 0 的实体对象（不是 null）—— `num()` 拦不住，于是「队列
 *     加载失败」会被画成「待分诊 0、进行中 0」，还带着能点进队列的链接。
 *   * 一行的四张卡有两套加载态（`adminLoading` / `statsLoading`）、两个失败原因，而
 *     它们说的是同一件事：这一栏现在什么样。
 *
 *  口径：`total` 是**全量**（公开 + 私密 + Agent 发现 + 安全问题），和下面那条曲线同
 *  一个板子。此前卡片走的是被 `PUBLIC_ONLY` 收窄的那一份，而曲线是全量 —— 于是「进行中」
 *  和「进行中」在卡片上和图上是两个数，两边各自都看着对。
 *
 *  新增/解决两张卡带窗口内逐日 spark 与上一窗口环比（`prev`，后端配套②）；四张卡
 *  的 `to` 都保留 —— 队列有对应的筛选口径，是真目的地。 */
const feedbackKpis = computed<KpiRow[]>(() => {
  const f = feedback.value
  const createdSum = f?.series.reduce((acc, row) => acc + row.created, 0)
  const resolvedSum = f?.series.reduce((acc, row) => acc + row.resolved, 0)
  return [
    {
      key: 'untriaged',
      label: t('feedback.dashboard.kpi.untriaged'),
      value: num(f?.total.unassigned),
      loading: store.statsLoading,
      to: queue({ assigned: 'none' }),
    },
    {
      key: 'inProgress',
      label: t('feedback.dashboard.kpi.inProgress'),
      value: num(f?.total.open),
      loading: store.statsLoading,
      to: queue({ status: 'in_progress' }),
    },
    {
      key: 'created',
      label: t('feedback.dashboard.kpi.createdInWindow', { d: store.statsDays }),
      value: createdSum === undefined ? '' : fmtNum(createdSum),
      loading: store.statsLoading,
      to: queue({ since: `${store.statsDays}d` }),
      spark: f?.series.map((row) => row.created) ?? [],
      ...deltaOf(createdSum, f?.prev?.created, fmtNum(f?.prev?.created ?? 0)),
    },
    {
      key: 'resolved',
      label: t('feedback.dashboard.kpi.resolvedInWindow', { d: store.statsDays }),
      value: resolvedSum === undefined ? '' : fmtNum(resolvedSum),
      loading: store.statsLoading,
      to: queue({ resolved_since: `${store.statsDays}d` }),
      spark: f?.series.map((row) => row.resolved) ?? [],
      ...deltaOf(resolvedSum, f?.prev?.resolved, fmtNum(f?.prev?.resolved ?? 0)),
    },
  ]
})

/** 队列那四栏的计数 —— **筛选，不是划分**（`agent` 是来源，和公开/私密重叠），所以
 *  四个数加起来不等于总数。这句话收在标题旁的口径 tip 里（见模板）。 */
const feedbackColumns = computed(() => {
  const c = feedback.value?.columns
  return [
    { key: 'public', label: t('feedback.dashboard.column.public'), value: num(c?.public) },
    { key: 'private', label: t('feedback.dashboard.column.private'), value: num(c?.private) },
    { key: 'agent', label: t('feedback.dashboard.column.agent'), value: num(c?.agent) },
    { key: 'security', label: t('feedback.dashboard.column.security'), value: num(c?.security) },
  ]
})

/** 梯子上的四级，全量并排。条的长度按四级里最大的那一级算 —— 四级是**同一量纲**的
 *  划分（加起来等于 `total.all`），所以可以同轴比长短。 */
const feedbackStatusRows = computed(() => {
  const s = feedback.value?.status
  if (!s) return []
  const rows = [
    { key: 'received', label: t('feedback.dashboard.status.received'), value: s.received },
    { key: 'in_progress', label: t('feedback.dashboard.status.inProgress'), value: s.in_progress },
    { key: 'resolved', label: t('feedback.dashboard.status.resolved'), value: s.resolved },
    { key: 'deployed', label: t('feedback.dashboard.status.deployed'), value: s.deployed },
  ]
  return rows
})

/** 压着没人管的急件 —— 只有它 > 0 时才画那一行警示。空着的时候不占位置。 */
const urgentOpen = computed(() => feedback.value?.total.urgent_open ?? 0)

/** 反馈的三条线：新增 / 解决 / 上线（§7.5：颜色由组件按线型发，这一层只管名字和值）。
 *
 *  **上线那一条是后补的，而它正是这条曲线在这里的理由**：梯子最后两档是两件事 ——
 *  「修好了」和「上线了」对提交者是两个不同的日子，而看板此前只画前两档，最该被看见的
 *  那一步整个不存在。后端的 `series[].deployed` 一直就回，只是没人读。 */
const feedbackSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.chart.created'),
    values: feedback.value?.series.map((row) => row.created) ?? [],
    style: 'solid',
  },
  {
    name: t('feedback.dashboard.chart.resolved'),
    values: feedback.value?.series.map((row) => row.resolved) ?? [],
    style: 'dashed',
  },
  {
    name: t('feedback.dashboard.chart.deployed'),
    values: feedback.value?.series.map((row) => row.deployed) ?? [],
    style: 'dotted',
  },
])

/** 迷你列表的行。「需处理」只有两种状态（还没人接手、有人在做）；更新时间取
 *  `last_activity_at`，为空退回 `created_at`。 */
const pending = computed(() =>
  store.adminItems
    .filter((row) => row.status === 'received' || row.status === 'in_progress')
    .slice(0, SHOWN)
    .map((row) => ({
      id: row.id,
      no: displayNo(row.display_id),
      title: row.title,
      status: row.status,
      updatedAt: row.last_activity_at ?? row.created_at,
    }))
)

/** 「FB-1042」里那串数字。列表左列要的是一个能纵向对齐、能排序的编号。 */
function displayNo(displayId: string): number {
  const digits = displayId.replace(/\D/g, '')
  return digits === '' ? 0 : Number(digits)
}

/* ---- 用量那一块 ---- */

/** 用量的 KPI。tokens/calls/cost 三张带逐日 spark 与上一窗口环比（`prev`）；成本与
 *  「算不出价钱的 token」两卡同挂一句口径 tip（它们是两张卡，不是页脚一行字 ——
 *  两件事各自是一个数，而「一行正文 + 一行脚注」那种写法把它们降级成了注释）。
 *
 *  **tokens/calls 曾经有 `to: queue()` 空筛选 —— 删了**：队列没有按用量筛选的口径，
 *  假 affordance 比不点更糟。成本/未定价两张本来就无 `to`，口径经 tip 给。 */
const usageKpis = computed<KpiRow[]>(() => {
  const u = usage.value
  const costNote = u ? t('feedback.dashboard.cost.note') : undefined
  return [
    {
      key: 'tokens',
      label: t('feedback.dashboard.usage.tokens'),
      value: num(u?.totals.tokens),
      loading: store.statsLoading,
      spark: u?.series.map((row) => row.tokens) ?? [],
      ...deltaOf(u?.totals.tokens, u?.prev?.tokens, fmtNum(u?.prev?.tokens ?? 0)),
    },
    {
      key: 'calls',
      label: t('feedback.dashboard.usage.calls'),
      value: num(u?.totals.calls),
      loading: store.statsLoading,
      spark: u?.series.map((row) => row.calls) ?? [],
      ...deltaOf(u?.totals.calls, u?.prev?.calls, fmtNum(u?.prev?.calls ?? 0)),
    },
    {
      key: 'cost',
      label: t('feedback.dashboard.cost.kpi'),
      value: u ? fmtCost(u.totals.cost_usd) : '',
      loading: store.statsLoading,
      spark: u?.series.map((row) => row.cost_usd) ?? [],
      ...deltaOf(u?.totals.cost_usd, u?.prev?.cost_usd, u?.prev ? fmtCost(u.prev.cost_usd) : ''),
      note: costNote,
    },
    {
      key: 'unpriced',
      label: t('feedback.dashboard.cost.unpricedLabel'),
      value: num(u?.totals.unpriced_tokens),
      loading: store.statsLoading,
      note: costNote,
    },
  ]
})

/** 窗口内每天的 token。**只画 token 一条**：调用次数和钱各自有不同的量级，三条线画在
 *  一根轴上会有两条贴着底走 —— 而「贴着底的那条是不是 0」正是这张图要回答的问题。
 *  calls/cost_usd 的去向是上面三张卡的 sparkline（量纲各自的迷你形状，不进同一张图）。 */
const usageSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.usage.tokens'),
    values: usage.value?.series.map((row) => row.tokens) ?? [],
    style: 'solid',
  },
])

/** 用量最高的几个项目。条只报 token —— 同一根条上再叠一个「花了多少钱」，它们的长度
 *  就各自代表不同的东西，而长短本来是用来比的。项目名原样给组件（它自己省略号）。
 *  行带 `project_id`（**身份**）与去向：整行一个链接，下钻到项目页。 */
const topProjects = computed(() =>
  (usage.value?.top_projects ?? []).map((row) => ({
    id: row.project_id,
    label: row.name,
    value: row.tokens,
    to: { path: `/projects/${row.project_id}` } as RouteLocationRaw,
  }))
)

/** 按模型拆。行本身带上钱与「算不出价」的两列，因为这一张表要回答的正是「贵的是
 *  模型还是计费方式」——只有 token 一列答不了。 */
const byModel = computed(() =>
  (usage.value?.by_model ?? []).map((row) => ({
    label: row.model || t('feedback.dashboard.usage.unknownModel'),
    value: row.tokens,
    cost: fmtCost(row.cost_usd),
    unpriced: row.unpriced_tokens > 0 ? fmtNum(row.unpriced_tokens) : '',
  }))
)

/** 按供给通路拆（gateway / subscription / native）。`subscription` 那一行的
 *  `unpriced_tokens` 就是 KPI 里「未定价 token」的来源 —— 这一张表是那张卡的注脚。 */
const byRoute = computed(() =>
  (usage.value?.by_route ?? []).map((row) => ({
    label: routeLabel(row.route),
    value: row.tokens,
    cost: fmtCost(row.cost_usd),
    unpriced: row.unpriced_tokens > 0 ? fmtNum(row.unpriced_tokens) : '',
  }))
)

/** 通路代号 → 人话。`''` 是打在补上这一列之前的那些行。 */
function routeLabel(route: string): string {
  if (route === 'gateway') return t('feedback.dashboard.usage.route.gateway')
  if (route === 'subscription') return t('feedback.dashboard.usage.route.subscription')
  if (route === 'native') return t('feedback.dashboard.usage.route.native')
  return t('feedback.dashboard.usage.route.unknown')
}

/** 平台的健康度。三格并排，**状态色只在这里用**（up / stalling / down）—— 全页别处
 *  都是中性阶，这一行是唯一需要「一眼看出好坏」的地方。 */
const health = computed(() => platform.value?.health ?? null)

/** 检查名 → 词条键。**写成字面量表**，不在模板里拼 `feedback.dashboard.health.${name}`
 *  —— 拼出来的键在源码里没有一处字面量出现，`catalog.spec.ts` 的「这个键没有任何文件
 *  引用」那条闸门会把它们判成死词条（它扫的是源码文本，不是运行时的调用）。和上面
 *  `TAB_KEY` 同一个理由。 */
const HEALTH_KEY: Record<string, string> = {
  database: 'feedback.dashboard.health.database',
  redis: 'feedback.dashboard.health.redis',
  event_loop: 'feedback.dashboard.health.event_loop',
}

const healthRows = computed(() => {
  const h = health.value
  if (!h) return []
  return Object.entries(h.checks).map(([name, body]) => ({
    key: name,
    label: t(HEALTH_KEY[name] ?? name),
    status: body.status,
    tone: body.status === 'up' ? 'ok' : body.status === 'down' ? 'danger' : 'warn',
  }))
})

/* ---- 性能那一块 ----
 *
 * 它是四类里唯一**读进程内存**的（另外三类读库），所以页面上必须把那三件口径说
 * 出来：没有窗口（只有此刻）、重启即清零、只覆盖业务 API。不说的话，读者会把它当
 * 成「整个平台的、有历史的」数 —— 而它两个都不是。perf.note 因此是**常驻**的那条
 * 注（每屏至多一条的额度给了它），不进 tip。
 */

/* ---- 交付管线（新） ---------------------------------------------------- */

const pipeline = computed(() => store.stats.pipeline)

/** 活四站：建卡 → 决议 → 合并 → 归档。**没有「闸门」那一站** —— 机器闸门已退役
 *  （#296），画上去就是一个永远空的站。四站各是**不同卡在不同时刻**的通过量，不是
 *  同一批样本的漏斗，所以 `AdminLiveSpine` 用导轨不用漏斗图。停留行挂 p90/最长的
 *  title 明细（站面只摆 p50，屏幕不被分位数淹）。 */
const spineStages = computed(() => {
  const p = pipeline.value
  if (!p) return []
  const backlog = p.backlog.by_status
  const accepted = backlog['accepted'] ?? 0
  const merged = p.dwell.filed_to_merge.count
  const filed = p.dwell.filed_to_decision.count + p.dwell.open_card_age.count
  const archived = p.dwell.accepted_not_archived
    ? Math.max(0, accepted - p.dwell.accepted_not_archived.count)
    : accepted
  return [
    {
      key: 'filed',
      label: t(STATION_KEY.filed),
      count: filed,
      dwellSeconds: p.dwell.open_card_age.p50_seconds,
      p90Seconds: null,
      maxSeconds: p.dwell.open_card_age.max_seconds,
    },
    {
      key: 'decided',
      label: t(STATION_KEY.decided),
      count: p.dwell.filed_to_decision.count,
      dwellSeconds: p.dwell.filed_to_decision.p50_seconds,
      p90Seconds: p.dwell.filed_to_decision.p90_seconds,
      maxSeconds: p.dwell.filed_to_decision.max_seconds,
    },
    {
      key: 'merged',
      label: t(STATION_KEY.merged),
      count: merged,
      dwellSeconds: p.dwell.filed_to_merge.p50_seconds,
      p90Seconds: p.dwell.filed_to_merge.p90_seconds,
      maxSeconds: p.dwell.filed_to_merge.max_seconds,
    },
    {
      key: 'archived',
      label: t(STATION_KEY.archived),
      count: archived,
      dwellSeconds: p.dwell.accepted_not_archived.max_seconds,
      p90Seconds: null,
      maxSeconds: p.dwell.accepted_not_archived.max_seconds,
    },
  ]
})

/** 卡点徽章。只在 >0 时出现 —— 空着时不占位置，也不画一个 0 的徽章。 */
const spineStuck = computed(() => {
  const p = pipeline.value
  if (!p) return []
  const rows: { label: string; count: number }[] = []
  if (p.backlog.stuck > 0) rows.push({ label: t('feedback.dashboard.pipeline.kpi.stuck'), count: p.backlog.stuck })
  if (p.needs_you.awaiting_answer > 0)
    rows.push({
      label: t('feedback.dashboard.pipeline.kpi.needsYou'),
      count: p.needs_you.awaiting_answer,
    })
  if (p.turn_failures.credits_refused > 0)
    rows.push({
      label: t('feedback.dashboard.credits.exhausted'),
      count: p.turn_failures.credits_refused,
    })
  return rows
})

/** 交付的五张卡。「递卡受阻」（`blocking_refile`）是非终态、会堵死整间房重新递卡
 *  的那部分积压 —— 它和「还在走」同挂一句 backlog 口径；「等你处理」挂三个理由的
 *  拆分明细（验收人/报告人/被点名）。pipeline 不配 delta：它以存量指标为主，没有
 *  可环比的流量合计（后端也不回 prev）。 */
const pipelineKpis = computed<KpiRow[]>(() => {
  const p = pipeline.value
  return [
    {
      key: 'live',
      label: t('feedback.dashboard.pipeline.kpi.live'),
      value: num(p?.backlog.live_total),
      loading: store.statsLoading,
      note: t('feedback.dashboard.pipeline.backlog.note'),
    },
    {
      key: 'stuck',
      label: t('feedback.dashboard.pipeline.kpi.stuck'),
      value: num(p?.backlog.stuck),
      loading: store.statsLoading,
    },
    {
      key: 'dwell',
      label: t('feedback.dashboard.pipeline.kpi.dwell'),
      value: hoursText(p?.dwell.filed_to_decision.p50_seconds ?? null),
      loading: store.statsLoading,
    },
    {
      key: 'needs',
      label: t('feedback.dashboard.pipeline.kpi.needsYou'),
      value: num(
        (p?.needs_you.reviewer_pending ?? 0) + (p?.needs_you.open_tasks ?? 0) + (p?.needs_you.awaiting_answer ?? 0)
      ),
      loading: store.statsLoading,
      note: p
        ? t('feedback.dashboard.pipeline.needs.breakdown', {
            r: p.needs_you.reasons.reviewer,
            p: p.needs_you.reasons.reporter,
            a: p.needs_you.reasons.asked,
          })
        : undefined,
    },
    {
      key: 'blockingRefile',
      label: t('feedback.dashboard.pipeline.kpi.blockingRefile'),
      value: num(p?.backlog.blocking_refile),
      loading: store.statsLoading,
      note: t('feedback.dashboard.pipeline.backlog.note'),
    },
  ]
})

const stuckRows = computed(() =>
  (pipeline.value?.stuck_cards ?? []).map((row) => ({
    id: row.card_id,
    title: row.change_subject || row.topic_title,
    subtitle: `${row.note_code ?? ''} ${row.reviewer_handle}`,
    statusLabel: row.status,
    tone: 'warn' as const,
    age: ageText(row.age_seconds),
    to: { path: `/topics/${row.topic_id}` },
  }))
)

const needsYouRows = computed(() =>
  (pipeline.value?.needs_you.items ?? []).map((row) => ({
    id: `${row.kind}:${row.id}`,
    title: row.title,
    subtitle:
      row.kind === 'task' ? t('feedback.dashboard.pipeline.needs.task') : t('feedback.dashboard.pipeline.needs.room'),
    tone: 'ink' as const,
    to: { path: `/topics/${row.topic_id}` },
  }))
)

const failureRows = computed(() => {
  const f = pipeline.value?.turn_failures
  if (!f) return []
  const rows = Object.entries(f.by_code)
    .filter(([, n]) => n > 0)
    .map(([code, n]) => ({
      label: t(FAIL_CODE_KEY[code] ?? code),
      value: n,
      cost: '—',
      unpriced: '—',
    }))
  if (f.other > 0)
    rows.push({
      label: t(FAIL_CODE_KEY.other),
      value: f.other,
      cost: '—',
      unpriced: '—',
    })
  if (f.credits_refused > 0)
    rows.push({
      label: t('feedback.dashboard.credits.exhausted'),
      value: f.credits_refused,
      cost: '—',
      unpriced: '—',
    })
  return rows
})

/** 机器连败的行。**没有 `to`**：平台里没有一页列设备，它曾经是跳回 `/admin` 的自链
 *  —— 假链接比不点更糟（设备列表页列为线外事项，有了再接上）。 */
const hostRows = computed(() =>
  (pipeline.value?.host_health.rows ?? []).map((row) => ({
    id: row.device_id,
    title: row.device_id,
    subtitle: row.last_failure_code ?? '',
    statusLabel: String(row.consecutive_failures),
    tone: row.quarantined_until ? ('danger' as const) : ('warn' as const),
  }))
)

/* ---- 产品健康（新） ---------------------------------------------------- */

const product = computed(() => store.stats.product)

const productKpis = computed<KpiRow[]>(() => [
  {
    key: 'north',
    label: t('feedback.dashboard.product.northStar', { d: store.statsDays }),
    value: num(product.value?.north_star.total),
    loading: store.statsLoading,
    spark: product.value?.north_star.series.map((row) => row.accepted) ?? [],
    ...deltaOf(
      product.value?.north_star.total,
      product.value?.north_star.prev_total,
      fmtNum(product.value?.north_star.prev_total ?? 0)
    ),
  },
  {
    key: 'returned',
    label: t('feedback.dashboard.product.rejection.title'),
    value:
      product.value?.rejection.returned_rate === null || product.value?.rejection.returned_rate === undefined
        ? ''
        : `${Math.round(product.value.rejection.returned_rate * 100)}%`,
    loading: store.statsLoading,
  },
  {
    key: 'useful',
    label: t('feedback.dashboard.product.usefulness.title'),
    value:
      product.value?.usefulness.useful_rate === null || product.value?.usefulness.useful_rate === undefined
        ? ''
        : `${Math.round(product.value.usefulness.useful_rate * 100)}%`,
    loading: store.statsLoading,
  },
  {
    key: 'dismiss',
    label: t('feedback.dashboard.product.usefulness.down'),
    value: num(product.value?.usefulness.proposal_dismissals),
    loading: store.statsLoading,
  },
])

const northSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.product.northStar', { d: store.statsDays }),
    values: product.value?.north_star.series.map((row) => row.accepted) ?? [],
    style: 'solid',
  },
])

const rejectionSegments = computed(() => {
  const b = product.value?.rejection.buckets
  if (!b) return []
  return [
    { label: t('feedback.dashboard.product.rejection.rejected'), value: b.rejected, shade: 'ink' as const },
    {
      label: t('feedback.dashboard.product.rejection.gate'),
      value: b.gate_failed + b.gate_blocked,
      shade: 'muted' as const,
    },
    { label: t('feedback.dashboard.product.rejection.conflict'), value: b.conflict, shade: 'muted' as const },
    { label: t('feedback.dashboard.product.rejection.voided'), value: b.voided, shade: 'faint' as const },
    {
      label: t('feedback.dashboard.product.rejection.revoked'),
      value: b.revoked_after_accept,
      shade: 'faint' as const,
    },
    { label: t('feedback.dashboard.product.rejection.live'), value: b.live, shade: 'faint' as const },
  ].filter((s) => s.value > 0)
})

const usefulnessSegments = computed(() => {
  const u = product.value?.usefulness
  if (!u) return []
  return [
    { label: t('feedback.dashboard.product.usefulness.up'), value: u.up, shade: 'ink' as const },
    { label: t('feedback.dashboard.product.usefulness.down'), value: u.down, shade: 'muted' as const },
    { label: t('feedback.dashboard.product.usefulness.unrated'), value: u.unrated_read, shade: 'faint' as const },
    { label: t('feedback.dashboard.product.usefulness.unread'), value: u.unread, shade: 'faint' as const },
  ].filter((s) => s.value > 0)
})

/* ---- 集成健康（新） ---------------------------------------------------- */

const integrations = computed(() => store.stats.integrations)

const integrationKpis = computed<KpiRow[]>(() => [
  {
    key: 'expired',
    label: t('feedback.dashboard.integrations.oauth.expired'),
    value: num(integrations.value?.oauth.expired),
    loading: store.statsLoading,
  },
  {
    key: 'norefresh',
    label: t('feedback.dashboard.integrations.oauth.noRefresh'),
    value: num(integrations.value?.oauth.no_refresh_token),
    loading: store.statsLoading,
  },
  {
    key: 'passkey',
    label: t('feedback.dashboard.integrations.passkey.title'),
    value:
      integrations.value?.passkey.coverage === null || integrations.value?.passkey.coverage === undefined
        ? ''
        : `${Math.round(integrations.value.passkey.coverage * 100)}%`,
    loading: store.statsLoading,
  },
  {
    key: 'dead',
    label: t('feedback.dashboard.integrations.delivery.dead'),
    value: num(integrations.value?.delivery.dead_letters),
    loading: store.statsLoading,
  },
])

/* ---- 四块补缺：额度燃尽 / 磁盘预览机器 / 投递事件 ---------------------- */

const credits = computed(() => usage.value?.credits ?? null)

const creditMeters = computed(() => {
  const c = credits.value
  if (!c) return []
  const rows: {
    label: string
    valueText: string
    limit: number | null
    ratio: number
    tone: 'ink' | 'ok' | 'warn' | 'danger'
    hint: string
  }[] = []
  for (const e of c.exhausted.slice(0, 5)) {
    rows.push({
      label: e.name,
      valueText: fmtNum(Math.round(e.credits_remaining)),
      limit: e.credits_total,
      ratio: 1,
      tone: 'danger',
      hint: t('feedback.dashboard.credits.exhausted'),
    })
  }
  for (const e of c.low.slice(0, 5)) {
    rows.push({
      label: e.name,
      valueText: fmtNum(Math.round(e.credits_remaining)),
      limit: e.credits_total,
      ratio: e.ratio,
      tone: 'warn',
      hint: t('feedback.dashboard.credits.low'),
    })
  }
  return rows
})

/** 「额度燃尽」标题旁的口径 tip：那句互斥集合的说明，追加按实价/扁平率的拆分明细
 *  （`credits.burn` 的两个分量，原是响应里没人读的两个数）。 */
const creditsNote = computed(() => {
  const base = t('feedback.dashboard.credits.note')
  const c = credits.value
  if (!c) return base
  return `${base} ${t('feedback.dashboard.credits.burnDetail', {
    priced: fmtNum(c.burn.priced_credits),
    flat: fmtNum(c.burn.flat_credits),
  })}`
})

const extras = computed(() => platform.value?.extras ?? null)

const extrasRows = computed(() => {
  const x = extras.value
  if (!x) return []
  const rows: {
    label: string
    valueText: string
    limit: number | null
    ratio: number
    tone: 'ink' | 'ok' | 'warn' | 'danger'
    hint: string
    note: string
  }[] = []
  if (x.disk.available && x.disk.used_pct !== undefined) {
    rows.push({
      label: t('feedback.dashboard.extras.disk.title'),
      valueText: `${x.disk.used_pct}%`,
      limit: 100,
      ratio: x.disk.used_pct / 100,
      tone: x.disk.tier === 'critical' ? 'danger' : x.disk.tier === 'warn' ? 'warn' : 'ink',
      hint: `${x.disk.free_gb} GB`,
      note: t('feedback.dashboard.extras.disk.note'),
    })
  }
  if (x.preview.available) {
    rows.push({
      label: t('feedback.dashboard.extras.preview.title'),
      valueText: num(x.preview.attached),
      limit: null,
      ratio: 0,
      tone: 'ink',
      hint: '',
      note: t('feedback.dashboard.extras.preview.note'),
    })
  }
  return rows
})

/** 常驻机器/项目机器的状态名 → 词条键。**字面量键表 + 原名兜底**（同 `HEALTH_KEY` 的
 *  模式）：拼出来的键 `catalog.spec.ts` 会判死键；台账里冒出表里没有的新状态时，
 *  原样显示状态名，不静默吞掉。 */
const MACHINE_STATE_KEY: Record<string, string> = {
  preparing: 'feedback.dashboard.extras.machineState.preparing',
  ready: 'feedback.dashboard.extras.machineState.ready',
  claimed: 'feedback.dashboard.extras.machineState.claimed',
  error: 'feedback.dashboard.extras.machineState.error',
}

const PROJECT_STATUS_KEY: Record<string, string> = {
  leased: 'feedback.dashboard.extras.projectStatus.leased',
  released: 'feedback.dashboard.extras.projectStatus.released',
  error: 'feedback.dashboard.extras.projectStatus.error',
}

/** 状态分布 → ShareBar 的段。按值降序，明度按 `ink → muted → faint` 顺次发
 *  （§2.7：不用色相区分系列）。空分布（台账全零）返回空数组 —— 那一格整个不渲染，
 *  不画一条「全是零」的假分布。 */
const STATE_SHADES = ['ink', 'muted', 'faint'] as const

function stateSegments(byState: Record<string, number> | undefined, keys: Record<string, string>) {
  return Object.entries(byState ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([state, n], i) => ({
      label: t(keys[state] ?? state),
      value: n,
      shade: STATE_SHADES[Math.min(i, STATE_SHADES.length - 1)],
    }))
}

const warmSegments = computed(() => stateSegments(extras.value?.machines.warm_by_state, MACHINE_STATE_KEY))
const projectMachineSegments = computed(() =>
  stateSegments(extras.value?.machines.project_by_status, PROJECT_STATUS_KEY)
)

const reliability = computed(() => {
  const p = store.stats.performance as (typeof store.stats.performance & { reliability?: any }) | null
  return p?.reliability ?? null
})

/* ---- 工具 -------------------------------------------------------------- */

/** 停留时长画成「N 分 / N 小时 / N 天」。没有读数画破折号，不画 0。 */
function hoursText(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return ''
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} ${t('feedback.dashboard.dwell.minutes')}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${t('feedback.dashboard.dwell.hours')}`
  const days = Math.floor(hours / 24)
  return `${days} ${t('feedback.dashboard.dwell.days')}`
}

function ageText(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t('feedback.dashboard.pipeline.age.minutes', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('feedback.dashboard.pipeline.age.hours', { h: hours })
  return t('feedback.dashboard.pipeline.age.days', { d: Math.floor(hours / 24) })
}

/** 站名。键表而不是拼字符串（同 `TAB_KEY` / `HEALTH_KEY`）。 */
const STATION_KEY: Record<string, string> = {
  filed: 'feedback.dashboard.pipeline.station.filed',
  decided: 'feedback.dashboard.pipeline.station.decided',
  merged: 'feedback.dashboard.pipeline.station.merged',
  archived: 'feedback.dashboard.pipeline.station.archived',
}

const productUnavailable = computed(
  () =>
    product.value?.unavailable.map((row) => ({
      name: row.name,
      text: t(PRODUCT_UNAVAILABLE_KEY[row.name] ?? row.name),
    })) ?? []
)

/** `name` 是 snake_case，词条是 camelCase。**键写全字面量**（同 `TAB_KEY`）：
 *  拼出来的键在源码里没有一处字面量出现，`catalog.spec.ts` 会把它判成死键。 */
const UNAVAILABLE_KEY: Record<string, string> = {
  github_app_permission_gaps: 'feedback.dashboard.integrations.unavailable.githubAppPermissionGaps',
  github_app_mint_failure_rate: 'feedback.dashboard.integrations.unavailable.githubAppMintFailureRate',
  login_lockout_stock_and_rate: 'feedback.dashboard.integrations.unavailable.loginLockoutStockAndRate',
  metering_post_freeze: 'feedback.dashboard.integrations.unavailable.meteringPostFreeze',
}

const PRODUCT_UNAVAILABLE_KEY: Record<string, string> = {
  acceptance_rate_after_summon: 'feedback.dashboard.product.unavailable.summon',
  churn_after_credits_exhausted: 'feedback.dashboard.product.unavailable.churn',
}

/** 轮次失败的码 → 词条。同 `HEALTH_KEY`：不拼字符串。 */
const FAIL_CODE_KEY: Record<string, string> = {
  turn_timeout: 'feedback.dashboard.pipeline.code.turn_timeout',
  prompt_undelivered: 'feedback.dashboard.pipeline.code.prompt_undelivered',
  host_unreachable: 'feedback.dashboard.pipeline.code.host_unreachable',
  storage_exhausted: 'feedback.dashboard.pipeline.code.storage_exhausted',
  runtime_image_missing: 'feedback.dashboard.pipeline.code.runtime_image_missing',
  subscription_credential_expired: 'feedback.dashboard.pipeline.code.subscription_credential_expired',
  workspace_vcs_perms: 'feedback.dashboard.pipeline.code.workspace_vcs_perms',
  other: 'feedback.dashboard.pipeline.code.other',
}

const integrationsUnavailable = computed(
  () =>
    integrations.value?.unavailable.map((row) => ({
      name: row.name,
      text: t(UNAVAILABLE_KEY[row.name] ?? row.name),
    })) ?? []
)

const perf = computed(() => store.stats.performance)

/** 一条路由的耗时。**`null` 画成「—」不是 0**：0 是一个读数（「真的很快」），
 *  `null` 是「这一格没有数据」。走 `fmtMs` 阶梯（ms → s → min）：p95 上一秒之后
 *  `1240 ms` 要自己心算，横幅上念不出来。 */
const ms = fmtMs

/** 进程跑了多久 —— 这一格回答的是「这份数据从什么时候开始算」。 */
const uptimeText = computed(() => {
  const total = perf.value?.uptime_seconds
  if (total === undefined) return ''
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return t('feedback.dashboard.perf.durationMinutes', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('feedback.dashboard.perf.durationHours', { h: hours, m: minutes % 60 })
  return t('feedback.dashboard.perf.durationDays', { d: Math.floor(hours / 24), h: hours % 24 })
})

const perfRoutes = computed(() => perf.value?.routes ?? [])

/** 路由表按 **p95 降序**（「哪条慢」的读法从上往下）。没样本的分位数（null）沉底，
 *  跟在「真的很快」后面而不是排在最前 —— 后端排好的序这里不重信， fixture 与旧响应
 *  都给出过未排序的数组。 */
const sortedPerfRoutes = computed(() => [...perfRoutes.value].sort((a, b) => (b.p95 ?? -1) - (a.p95 ?? -1)))

/** 「此刻最慢」横幅的那一条 —— 排序后的第一行，和导轨短值（`slowestP95`）同一个口径。 */
const slowestRoute = computed(() => sortedPerfRoutes.value[0] ?? null)

/** 路由表默认只摆最慢的前 N 条。这一屏的问题是「哪条慢」：快路由的长尾全铺开会把
 *  下面的网络/投递挤出两屏，而它们 p95 最高也就折叠行上写的那点 —— 折叠行本身就是
 *  结论。不用面板内滚动条：嵌套滚动在页面里手感很差。 */
const PERF_TOP_N = 8

const routeFilter = ref('')
const perfFoldOpen = ref(false)

/** 筛选作用于**全部**路由（不受 Top N 折叠限制）：找具体某条接口时，它可能正被折着。 */
const filteredPerfRoutes = computed(() => {
  const q = routeFilter.value.trim().toLowerCase()
  if (!q) return sortedPerfRoutes.value
  return sortedPerfRoutes.value.filter((row) => `${row.method} ${row.route}`.toLowerCase().includes(q))
})

const visiblePerfRoutes = computed(() =>
  routeFilter.value.trim() || perfFoldOpen.value
    ? filteredPerfRoutes.value
    : filteredPerfRoutes.value.slice(0, PERF_TOP_N)
)

/** 折叠行的文案；不需要折叠（筛选中 / 总数不超 N）时是 null，按钮不渲染。 */
const perfFold = computed(() => {
  if (routeFilter.value.trim() || filteredPerfRoutes.value.length <= PERF_TOP_N) return null
  if (perfFoldOpen.value) return t('feedback.dashboard.perf.foldLess')
  const firstFolded = filteredPerfRoutes.value[PERF_TOP_N]
  return t('feedback.dashboard.perf.foldMore', {
    n: filteredPerfRoutes.value.length - PERF_TOP_N,
    p95: firstFolded?.p95 === null || firstFolded?.p95 === undefined ? '—' : ms(firstFolded.p95),
  })
})

/** p95 微型量级条的归一分母：**全表**最大值（不是可见行的 —— 折叠/筛选不该改变
 *  同一根条的含义，否则「剩下来的看着都挺慢」）。 */
const maxP95 = computed(() => Math.max(1, ...perfRoutes.value.map((row) => row.p95 ?? 0)))

/** 条宽百分比。下限 4%：一个极小值不能让「这一条在榜上」从屏幕上消失。 */
function p95BarWidth(v: number | null): string {
  if (v === null || v === undefined) return '0%'
  return `${Math.max(4, (v / maxP95.value) * 100)}%`
}

/** 「真的慢」的阈值：p95 ≥ 1s 的条点琥珀。全页唯一的警示色份额给它 —— 其余条保持
 *  墨色，层级靠长短表达。 */
const HOT_P95_MS = 1000

function isHotP95(v: number | null): boolean {
  return v !== null && v !== undefined && v >= HOT_P95_MS
}

/** 展开着的路由行（`${method} ${route}`）。行首 chevron 整行一个按钮；展开行内嵌
 *  这条路由的分钟级 spark（响应里一直回、此前没人读的 24 个点）。 */
const expandedRoute = ref<string | null>(null)

function toggleRoute(key: string) {
  expandedRoute.value = expandedRoute.value === key ? null : key
}

/** spark 全 null 的路由没有可展开的东西 —— chevron 进禁用态（不是藏起来：同一列的
 *  图标有有无无，比「有的行窄一截」好读）。 */
function sparkDead(spark: (number | null)[]): boolean {
  return spark.every((v) => v === null)
}

/** 被截断的那部分要说出来：表里只有前 N 条，写「12 条」而不写「共 34 条」的话，
 *  读者会以为这就是全部。 */
/** 「有样本 X / 共 Y」—— 分母是注册的全部路由。
 *
 *  只报 X 会被读成「这个 app 才 6 条路由」（管理员就问过「在采的路由是不是太少了」）。
 *  实际上 X 是**重启以来被访问过、留下样本的**那几条，没被访问过的路由在这里根本
 *  不出现。两个数一起读才答得了「是不是太少了」。 */
const routesText = computed(() => {
  const p = perf.value
  if (!p) return ''
  const registered = p.routes_registered
  // **每一条注册过的端点都占一行**（没样本的也在），所以这里报的是「有样本 X / 共 Y」。
  // 截断（线上护栏）必须说出来：静默截断读起来像「就这些」。
  const shown = p.routes_omitted
    ? t('feedback.dashboard.perf.routesTruncated', {
        shown: p.routes.length,
        total: p.routes_registered ?? p.routes.length,
      })
    : null
  if (shown) return shown
  return registered === undefined || registered === null
    ? String(p.routes_with_samples)
    : t('feedback.dashboard.perf.routesOf', {
        shown: p.routes_with_samples,
        total: registered,
      })
})

/** 路由表块头那句口径（收进 tip）：routesNote 原话，溢出丢弃的样本数 >0 时追加一句
 *  —— 静默丢弃读起来像「就这些」。 */
const perfTableNote = computed(() => {
  const base = t('feedback.dashboard.perf.routesNote')
  const dropped = perf.value?.dropped_series ?? 0
  return dropped > 0 ? `${base} ${t('feedback.dashboard.perf.dropped', { n: dropped })}` : base
})

/** 网络吞吐的短读数。**读不到画「—」，绝不画 0** —— 0 说「网是闲的」，null 说
 *  「看不见」，两者在屏幕上必须长得不一样。 */
function bps(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${fmtSI(v, 'B/s')}`
}

const netUplink = computed(() => perf.value?.network?.uplink)
const netApi = computed(() => perf.value?.network?.api)

const lagText = computed(() => {
  const lag = perf.value?.loop_lag
  return lag === undefined ? '' : `${lag.recent_ms} ms`
})

/* ---- 平台那一块 ---- */

const peopleKpis = computed<KpiRow[]>(() => [
  {
    key: 'accounts',
    label: t('feedback.dashboard.people.total'),
    value: num(platform.value?.people.total),
    loading: store.statsLoading,
  },
  // 真人 / agent 分开报，不是一个总数让人自己猜。判据是 `agent_bindings`（和后端
  // `IdentityService.is_agent` 同一份），所以这两个数必然加得回 `total`。
  {
    key: 'humans',
    label: t('feedback.dashboard.people.humans'),
    value: num(platform.value?.people.humans),
    loading: store.statsLoading,
  },
  {
    key: 'agents',
    label: t('feedback.dashboard.people.agents'),
    value: num(platform.value?.people.agents),
    loading: store.statsLoading,
  },
  {
    key: 'new',
    label: t('feedback.dashboard.people.newInWindow', { d: store.statsDays }),
    value: num(platform.value?.people.new),
    loading: store.statsLoading,
    spark: platform.value?.people.series.map((row) => row.created) ?? [],
    ...deltaOf(
      platform.value?.people.new,
      platform.value?.people.prev_new,
      fmtNum(platform.value?.people.prev_new ?? 0)
    ),
  },
  {
    key: 'admins',
    label: t('feedback.dashboard.people.admins'),
    value: num(platform.value?.people.admins),
    loading: store.statsLoading,
  },
])

/** 新增账号的逐日曲线：**真人 / Agent 两条**（拆分列一直在响应里，此前没人读）。
 *  真人走实线（`--text`），Agent 走虚线（`--muted`）—— 系列语义由图例文字承担，
 *  不靠色相。 */
const signupSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.people.humans'),
    values: platform.value?.people.series.map((row) => row.human_created) ?? [],
    style: 'solid',
  },
  {
    name: t('feedback.dashboard.people.agents'),
    values: platform.value?.people.series.map((row) => row.agent_created) ?? [],
    style: 'dashed',
  },
])

/** 机器那四行。**存量，不是在线数** —— 在线状态住在进程内存里，库里没有可以查的那一
 *  列（`platform_stats/repositories.py` 的模块 docstring）。这句话必须写在页面上：一个
 *  「机器 5」的数字，读的人默认会当成「现在有 5 台在跑」。它是平台屏**常驻**的那条注
 *  （每屏至多一条的额度给了它），不进 tip。 */
const machines = computed(() => [
  { key: 'devices', label: t('feedback.dashboard.machines.devices'), value: num(platform.value?.machines.devices) },
  {
    key: 'hosted',
    label: t('feedback.dashboard.machines.hosted'),
    value: num(platform.value?.machines.hosted_devices),
  },
  { key: 'warm', label: t('feedback.dashboard.machines.warm'), value: num(platform.value?.machines.warm_machines) },
  {
    key: 'project',
    label: t('feedback.dashboard.machines.project'),
    value: num(platform.value?.machines.project_machines),
  },
])

/** 日期轴的标签。**按类显式取 series**（产品类此前落到 feedback 的 series 上 ——
 *  今天恰好同窗口所以没错，但那是隐性依赖，不是设计）：product → `north_star.series`、
 *  feedback → `feedback.series`、usage → `usage.series`、platform → `people.series`。 */
const xLabels = computed(() => {
  const series =
    kind.value === 'platform'
      ? platform.value?.people.series
      : kind.value === 'usage'
        ? usage.value?.series
        : kind.value === 'product'
          ? product.value?.north_star.series
          : feedback.value?.series
  return (series ?? []).map((row) => dayLabel(row.date))
})

/** 轴标签写「9/15」：轴上七个点，写全年月日是七串数字挤在一起，而窗口在页头上已经
 *  说了是几天。 */
function dayLabel(date: string): string {
  // 后端给的是 `YYYY-MM-DD`（或带时间的 ISO 串），取前两段就够，别交给 `Date` 去解析
  // —— 那会按本地时区把日期挪一天。
  const [, month, day] = date.slice(0, 10).split('-')
  return `${Number(month)}/${Number(day)}`
}

/** 点某一天去队列看那一天收进来的。只有反馈那一类的图接这个事件：用量和平台没有对应
 *  的筛选参数，接了就只是「点了没反应」。 */
function onSelectDay(index: number) {
  const date = feedback.value?.series[index]?.date
  if (!date) return
  void router.push(queue({ since: date.slice(0, 10) }))
}

/* ---- 时效性：时间戳、轮询、重试 ------------------------------------------ */

/** 「HH:MM」（补零）。直接读 `Date` 的时分，不把字符串交给 `Date` 解析。 */
function hhmm(at: number): string {
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 页头那句「更新于 HH:MM」。当前类还没成功拉到过（`statsAt` 为 null）时是空串；
 *  超过 5 分钟追加「· N 分钟前」—— 切回旧类时一眼看出这份数据有多旧。 */
const stampText = computed(() => {
  const at = store.statsAt[kind.value]
  if (at === null) return ''
  const ageMin = Math.floor((Date.now() - at) / 60_000)
  return ageMin >= 5
    ? t('feedback.dashboard.updatedAtStale', { time: hhmm(at), n: ageMin })
    : t('feedback.dashboard.updatedAt', { time: hhmm(at) })
})

/** 导轨卡的 `title`：口径提示句，加上那一类的更新时刻（§10.4 —— 常驻年龄行是
 *  噪音，但指针停上去时「这份数据是什么时候的」要拿得到）。 */
function kindTitle(k: StatsKind): string {
  const hint = pulseByKey.value[k]?.hint ?? ''
  const at = store.statsAt[k]
  if (at === null) return hint
  return `${hint} · ${t('feedback.dashboard.updatedAt', { time: hhmm(at) })}`
}

/** 轮询只覆盖「这一刻」的两类（平台健康、接口耗时），60s。窗口类（交付/产品/反馈/
 *  用量）有手动 R 和切窗口已经足够；integrations 是存量慢变（它的死信/未发在
 *  performance.reliability 里有同源读数，沾性能类的轮询光）。 */
const POLL_KINDS: StatsKind[] = ['platform', 'performance']
const POLL_MS = 60_000

let pollTimer: number | undefined

function pollTick() {
  if (document.visibilityState !== 'visible') return
  if (!POLL_KINDS.includes(kind.value)) return
  void store.loadStats(kind.value, store.statsDays)
}

/** 回到可见时补一次（如果这一类已经旧过一个周期）。轮询失败不打扰 —— `loadStats`
 *  失败只写 `store.error`，旧数据配旧时间戳还在，错误块只在「什么都没拿到」时整屏。 */
function onVisibleAgain() {
  if (document.visibilityState !== 'visible') return
  if (!POLL_KINDS.includes(kind.value)) return
  const at = store.statsAt[kind.value]
  if (at !== null && Date.now() - at > POLL_MS) void store.loadStats(kind.value, store.statsDays)
}

/** 错误块的重试：**真重拉**当前这一类；反馈类顺带把迷你列表那一路也重拉
 *  （它走的是 `loadAdmin`，不在 `loadStats` 里）。 */
function retry() {
  void store.loadStats(store.statsKind, store.statsDays)
  if (store.statsKind === 'feedback') void store.loadAdmin()
}

onMounted(() => {
  // 默认落点是**交付**（store 的 statsKind 初始值）：管理员早上第一个问题是
  // 「现在该我动的是哪几件」，不是「今天 token 多少」。用户显式选过的分类由
  // store 记着，重挂载原样恢复 —— 这里只补拉「当前这一类还没有数据」的情形。
  //
  // `loadStats` 只写 `statsBusy`、不在完成前写 `stats`，所以「切片还是 null 就拉」
  // 在首次挂载时**必然**成立一次 —— 这正是要的；但不要在上面的分支之外再补第二句，
  // 否则同一分类两个并发请求，store 的序号守卫（feedback.ts 的 `statsSeq`）会把先
  // 回来的那个响应丢掉。
  if (store.stats[store.statsKind] === null) {
    void store.loadStats(store.statsKind, store.statsDays)
  }
  // 两件事并发：队列那一路给 `counts` 和迷你列表的十行（反馈分类要），看板那一路给当前
  // 分类的曲线。串行只会让首屏多等一个来回。
  void store.loadAdmin()

  pollTimer = window.setInterval(pollTick, POLL_MS)
  document.addEventListener('visibilitychange', onVisibleAgain)
})

onBeforeUnmount(() => {
  window.clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibleAgain)
})
</script>

<template>
  <div class="ad">
    <div class="ad__inner page-container--admin">
      <header class="ad__head">
        <div class="ad__head-row">
          <h1 class="t-console-title">{{ t('feedback.dashboard.title') }}</h1>
          <div class="ad__head-side">
            <!-- 统计窗口 7/30/90。只有窗口类（`WINDOWED_KINDS`）给这个切换器：
                 性能读进程内存、集成是存量，它们没有「过去 N 天」—— 摆着是个假开关。
                 切窗口由 `setStatsDays` 把已加载的窗口类全部重拉（缓存键=窗口）。
                 下划线小页签，和下面的分类页签同一种语言：整页没有框状切换钮。 -->
            <div
              v-if="WINDOWED_KINDS.includes(store.statsKind)"
              class="ad__wintabs"
              role="group"
              :aria-label="t('feedback.dashboard.window.switchAria')"
            >
              <button
                type="button"
                class="ad__wintab"
                :class="{ 'ad__wintab--on': store.statsDays === 7 }"
                :aria-pressed="store.statsDays === 7"
                @click="store.setStatsDays(7)"
              >
                {{ t('feedback.dashboard.window.d7') }}
              </button>
              <button
                type="button"
                class="ad__wintab"
                :class="{ 'ad__wintab--on': store.statsDays === 30 }"
                :aria-pressed="store.statsDays === 30"
                @click="store.setStatsDays(30)"
              >
                {{ t('feedback.dashboard.window.d30') }}
              </button>
              <button
                type="button"
                class="ad__wintab"
                :class="{ 'ad__wintab--on': store.statsDays === 90 }"
                :aria-pressed="store.statsDays === 90"
                @click="store.setStatsDays(90)"
              >
                {{ t('feedback.dashboard.window.d90') }}
              </button>
            </div>
            <span class="ad__stamp t-meta-read">{{ stampText }}</span>
          </div>
        </div>

        <!-- 分类控件是这一页的**第一个控件**，也是**唯一**一条目的地导轨：读的人先决定
             看哪一类，再看数字。下划线页签（不是迷你卡）：七张卡片把「页面里又嵌了一个
             仪表盘」，切换控件该安静地待在页首。每一块最该被看见的那个数（短值）与状态
             点留在页签上 —— 第一信息层没丢，丢的只是框。

             短值是**附属读数**，不是这个按钮的可访问名字：`aria-hidden` 掉它和状态点，
             按钮的 accessible name 保持裸标签（`交付` / `用量` …）。否则 e2e 里
             `getByRole('button', { name: '反馈', exact: true })` 会因为名字变成
             「反馈 待分诊 3」而永远匹配不上。提示句放在 `title` 上，够指针用户读。
             窄了横向滚动不换行 —— 换行会把页签堆成一面墙。 -->
        <div class="ad__kinds" role="group" :aria-label="t('feedback.dashboard.kindsAria')">
          <button
            v-for="k in KINDS"
            :key="k"
            type="button"
            class="ad__kind"
            :class="{ 'ad__kind--on': k === store.statsKind }"
            :aria-pressed="k === store.statsKind"
            :title="kindTitle(k)"
            @click="selectKind(k)"
          >
            <span
              v-if="pulseByKey[k] && pulseByKey[k]!.tone !== 'ink'"
              class="ad__kind-dot"
              :class="`ad__kind-dot--${pulseByKey[k]!.tone}`"
              aria-hidden="true"
            />
            <span class="ad__kind-label">{{ t(TAB_KEY[k]) }}</span>
            <span class="ad__kind-val t-num" :class="`ad__kind-val--${pulseByKey[k]!.tone}`" aria-hidden="true">{{
              pulseByKey[k]!.value
            }}</span>
          </button>
        </div>
      </header>

      <!-- 错误是**整块**的（§9.3）：页头留着 —— 它是这一页的名字，不是数据。错误
           正文是**服务端原话**（不改写），重试是唯一主操作（琥珀份额归它），而且
           真重拉 —— 不是把错误状态清掉装没事。 -->
      <div v-if="failed" class="ad__none">
        <span class="ad__none-title">{{ t('feedback.dashboard.error.title') }}</span>
        <span class="ad__none-desc">{{ store.error }}</span>
        <v-btn color="primary" size="small" class="ad__retry" @click="retry">
          {{ t('feedback.dashboard.retry') }}
        </v-btn>
      </div>

      <!-- 交付管线：产品自己的主链。默认落点 —— 「现在该我动的是哪几件」排第一。 -->
      <template v-else-if="kind === 'pipeline'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in pipelineKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
            :note="kpi.note"
          />
        </div>

        <!-- 活四站导轨。**没有闸门那一站** —— 它已退役（#296），画上去就是一个永远
             空的站，而页面第一眼的位置不该放装饰。 -->
        <AdminLiveSpine :stages="spineStages" :stuck="spineStuck" :loading="store.statsLoading" />

        <div class="ad__row">
          <AdminActionList
            :title="t('feedback.dashboard.pipeline.needs.title')"
            :rows="needsYouRows"
            :loading="store.statsLoading"
            :empty="t('feedback.dashboard.pipeline.needs.empty')"
            :note="t('feedback.dashboard.pipeline.needs.note')"
          />
          <AdminActionList
            :title="t('feedback.dashboard.pipeline.stuck.title')"
            :rows="stuckRows"
            :loading="store.statsLoading"
            :empty="t('feedback.dashboard.pipeline.stuck.empty')"
            :note="t('feedback.dashboard.pipeline.stuck.note')"
          />
        </div>

        <div class="ad__row ad__row--equal">
          <AdminBreakTable
            :title="t('feedback.dashboard.pipeline.failures.title')"
            :note="t('feedback.dashboard.pipeline.failures.note')"
            :rows="failureRows"
            :loading="store.statsLoading"
          />
          <AdminActionList
            :title="t('feedback.dashboard.pipeline.host.title')"
            :rows="hostRows"
            :loading="store.statsLoading"
            :empty="t('feedback.dashboard.pipeline.host.empty')"
            :note="t('feedback.dashboard.pipeline.host.note')"
          />
        </div>
      </template>

      <!-- 产品健康：北极星 + 两条护栏 + 两条「今天算不出来」。 -->
      <template v-else-if="kind === 'product'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in productKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
            :delta="kpi.delta"
            :delta-title="kpi.deltaTitle"
            :spark="kpi.spark"
          />
        </div>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.product.northStar', { d: store.statsDays })"
            :x-labels="xLabels"
            :series="northSeries"
            :loading="store.statsLoading"
            :note="t('feedback.dashboard.product.northStarNote')"
          />
          <AdminShareBar
            :title="t('feedback.dashboard.product.rejection.title')"
            :segments="rejectionSegments"
            :note="t('feedback.dashboard.product.rejection.note')"
            :loading="store.statsLoading"
          />
        </div>

        <AdminShareBar
          :title="t('feedback.dashboard.product.usefulness.title')"
          :segments="usefulnessSegments"
          :note="t('feedback.dashboard.product.usefulness.note')"
          :loading="store.statsLoading"
        />

        <!-- 算不出来的那两条：**带理由**，不画一个假 0。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">
            {{ t('feedback.dashboard.product.unavailable.title')
            }}<AdminNoteTip :text="t('feedback.dashboard.product.unavailable.note')" />
          </h2>
          <p v-for="row in productUnavailable" :key="row.name" class="ad__none-desc t-meta-read">
            {{ row.text }}
          </p>
        </section>
      </template>

      <!-- 集成健康：静默降级。**没有 days** —— 凭据与投递是存量问题。 -->
      <template v-else-if="kind === 'integrations'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in integrationKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
          />
        </div>

        <div class="ad__row ad__row--equal">
          <AdminShareBar
            :title="t('feedback.dashboard.integrations.oauth.title')"
            :segments="
              integrations
                ? [
                    {
                      label: t('feedback.dashboard.integrations.oauth.expired'),
                      value: integrations.oauth.expired,
                      shade: 'ink' as const,
                    },
                    {
                      label: t('feedback.dashboard.integrations.oauth.expiring'),
                      value: integrations.oauth.expiring_7d,
                      shade: 'muted' as const,
                    },
                    {
                      label: t('feedback.dashboard.integrations.oauth.noRefresh'),
                      value: integrations.oauth.no_refresh_token,
                      shade: 'faint' as const,
                    },
                  ].filter((s) => s.value > 0)
                : []
            "
            :note="t('feedback.dashboard.integrations.oauth.note')"
            :loading="store.statsLoading"
          />
          <AdminMeterBar
            :label="t('feedback.dashboard.integrations.passkey.title')"
            :value-text="integrations ? `${integrations.passkey.with_passkey} / ${integrations.passkey.accounts}` : ''"
            :limit="integrations?.passkey.accounts ?? null"
            :ratio="integrations?.passkey.coverage ?? 0"
            :hint="
              integrations?.passkey.coverage === null || integrations?.passkey.coverage === undefined
                ? ''
                : `${Math.round(integrations.passkey.coverage * 100)}%`
            "
            :note="t('feedback.dashboard.integrations.passkey.note')"
            :loading="store.statsLoading"
          />
        </div>

        <p class="ad__block-note t-meta-read">
          {{ t('feedback.dashboard.integrations.oauth.total') }} {{ integrations?.oauth.total ?? '—' }}
        </p>

        <AdminShareBar
          :title="t('feedback.dashboard.integrations.delivery.title')"
          :segments="
            integrations
              ? [
                  {
                    label: t('feedback.dashboard.integrations.delivery.unsent'),
                    value: integrations.delivery.unsent,
                    shade: 'ink' as const,
                  },
                  {
                    label: t('feedback.dashboard.integrations.delivery.dead'),
                    value: integrations.delivery.dead_letters,
                    shade: 'muted' as const,
                  },
                ].filter((s) => s.value > 0)
              : []
          "
          :note="t('feedback.dashboard.integrations.delivery.note')"
          :loading="store.statsLoading"
        />
        <!-- 最早待补发的那一封是多久以前的（相对时间）。`null` = 没有待补发，不画。 -->
        <p v-if="integrations?.delivery.oldest_unsent_at" class="ad__block-note t-meta-read">
          {{
            t('feedback.dashboard.integrations.delivery.oldest', {
              time: relTime(integrations.delivery.oldest_unsent_at),
            })
          }}
        </p>

        <section class="ad__split">
          <h2 class="ad__block-title">
            {{ t('feedback.dashboard.integrations.unavailable.title')
            }}<AdminNoteTip :text="t('feedback.dashboard.integrations.unavailable.note')" />
          </h2>
          <p v-for="row in integrationsUnavailable" :key="row.name" class="ad__none-desc t-meta-read">
            {{ row.text }}
          </p>
        </section>
      </template>

      <!-- 反馈：栏位计数 + 两条曲线 + 需处理那十行。 -->
      <template v-else-if="kind === 'feedback'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in feedbackKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
            :to="kpi.to"
            :delta="kpi.delta"
            :delta-title="kpi.deltaTitle"
            :spark="kpi.spark"
          />
        </div>

        <!-- 急件警示：只有真的压着没人管的急件时才画。空着时不占位置。 -->
        <p v-if="urgentOpen > 0" class="ad__urgent t-meta">
          {{ t('feedback.dashboard.kpi.urgent', { n: urgentOpen }) }}
        </p>

        <!-- 四栏计数。**筛选不是划分**，所以口径在标题旁的 tip 里等着。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">
            {{ t('feedback.dashboard.column.title') }}<AdminNoteTip :text="t('feedback.dashboard.column.note')" />
          </h2>
          <div class="ad__split-grid">
            <div v-for="col in feedbackColumns" :key="col.key" class="ad__split-cell">
              <span class="ad__split-label t-eyebrow-read">{{ col.label }}</span>
              <span class="ad__split-value t-console-title t-num">{{ col.value }}</span>
            </div>
          </div>
        </section>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.chart.title')"
            :x-labels="xLabels"
            :series="feedbackSeries"
            :loading="store.statsLoading"
            @select="onSelectDay"
          />
          <AdminNumberList
            :title="t('feedback.dashboard.list.title', { count: pending.length })"
            :rows="pending"
            :loading="store.adminLoading"
            :more-to="queue()"
          />
        </div>

        <!-- 状态分布：四级同轴（它们加起来等于总数，所以可以比长短）。 -->
        <AdminBarChart
          :title="t('feedback.dashboard.status.title')"
          :rows="feedbackStatusRows"
          :loading="store.statsLoading"
        />
      </template>

      <!-- 用量：窗口内的 token 与调用次数、每天一条线、最花钱的几个项目、成本那一行。 -->
      <template v-else-if="kind === 'usage'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in usageKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
            :delta="kpi.delta"
            :delta-title="kpi.deltaTitle"
            :spark="kpi.spark"
            :note="kpi.note"
          />
        </div>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.usage.chart')"
            :x-labels="xLabels"
            :series="usageSeries"
            :loading="store.statsLoading"
          />
          <AdminBarChart :title="t('feedback.dashboard.usage.top')" :rows="topProjects" :loading="store.statsLoading" />
        </div>

        <!-- 成本与「未定价 token」这两件事是上面那两张 KPI 卡，它们之间的关系那句
             （金额里没有「算不出价钱」的部分）收在两卡各自的口径 tip 里。 -->

        <!-- 两个正交的拆分：模型答「贵的是哪个模型」，通路答「贵的是计费方式还是模型」。
             并排放是因为**只有两个一起看**才答得出那个问题。 -->
        <div class="ad__row ad__row--equal">
          <AdminBreakTable
            :title="t('feedback.dashboard.usage.byModel')"
            :note="t('feedback.dashboard.usage.byModelNote')"
            :rows="byModel"
            :loading="store.statsLoading"
          />
          <AdminBreakTable
            :title="t('feedback.dashboard.usage.byRoute')"
            :note="t('feedback.dashboard.usage.byRouteNote')"
            :rows="byRoute"
            :loading="store.statsLoading"
          />
        </div>

        <!-- 额度燃尽：三个互斥名单（已耗尽 / 快烧完 / 不限量）。少了它，三个项目同时
             停摆时 token 曲线只是「今天用量下降」，看起来像好消息。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">
            {{ t('feedback.dashboard.credits.title') }}<AdminNoteTip :text="creditsNote" />
          </h2>
          <div class="ad__split-grid">
            <AdminMeterBar
              v-for="row in creditMeters"
              :key="row.label"
              :label="row.label"
              :value-text="row.valueText"
              :limit="row.limit"
              :ratio="row.ratio"
              :tone="row.tone"
              :hint="row.hint"
              :loading="store.statsLoading"
            />
          </div>
          <!-- 不限量数 / 燃烧速率 / 预计用尽：这是**数据行**不是注脚，原位保留。 -->
          <p v-if="credits" class="ad__block-note t-meta-read">
            {{ t('feedback.dashboard.credits.unlimited') }} {{ credits.unlimited_count }} ·
            {{ t('feedback.dashboard.credits.burn') }} {{ credits.burn.credits_per_day.toFixed(1) }}/d ·
            {{ t('feedback.dashboard.credits.eta') }} —
          </p>
        </section>
      </template>

      <!-- 性能：**这一刻**的接口耗时。它是四类里唯一读进程内存的，所以底下那句口径
           不是装饰 —— 少了它，这些数会被读成「有历史的、整个平台的」。
           这一屏要回答的问题只有一个：**哪条慢**。所以最慢那条不在表里等人找，
           直接一条横幅给结论；表按 p95 降序，默认只摆前 8 条（快路由的长尾是噪音），
           找具体某条交给筛选框。 -->
      <template v-else-if="kind === 'performance'">
        <div class="ad__kpis">
          <AdminKpiCard
            :label="t('feedback.dashboard.perf.active')"
            :value="num(perf?.active_requests)"
            :loading="store.statsLoading"
          />
          <AdminKpiCard
            :label="t('feedback.dashboard.perf.uptime')"
            :value="uptimeText"
            :loading="store.statsLoading"
          />
          <AdminKpiCard :label="t('feedback.dashboard.perf.lag')" :value="lagText" :loading="store.statsLoading" />
          <AdminKpiCard
            :label="t('feedback.dashboard.perf.routes')"
            :value="routesText"
            :loading="store.statsLoading"
          />
        </div>

        <!-- 最慢那条的横幅：左侧一道墨线，不是卡片 —— 它是这一屏的结论，不是又一块
             内容。路由表默认按 p95 降序（见 `sortedPerfRoutes`），第一行就是它。 -->
        <div v-if="slowestRoute" class="ad__slowest">
          <span class="ad__slowest-label">{{ t('feedback.dashboard.perf.banner') }}</span>
          <span class="ad__slowest-route">
            <span class="ad__perf-method">{{ slowestRoute.method }}</span>
            <span class="ad__perf-path num-leaf">{{ slowestRoute.route }}</span>
          </span>
          <span class="ad__slowest-val">
            <b class="ad__slowest-num t-num">{{ ms(slowestRoute.p95) }}</b>
            <span class="ad__slowest-meta t-meta-read">
              {{ t('feedback.dashboard.perf.bannerMeta', { n: fmtNum(slowestRoute.count) }) }}
            </span>
          </span>
        </div>

        <div class="ad__perf">
          <div class="ad__perf-head">
            <h2 class="ad__block-title">
              {{ t('feedback.dashboard.perf.tableTitle') }}<AdminNoteTip :text="perfTableNote" />
            </h2>
            <!-- 找具体某条接口的入口。筛选时不受 Top 8 折叠限制（见 visiblePerfRoutes）。 -->
            <input
              v-model="routeFilter"
              class="ad__perf-filter"
              type="text"
              autocomplete="off"
              :placeholder="t('feedback.dashboard.perf.filterPlaceholder')"
              :aria-label="t('feedback.dashboard.perf.filterPlaceholder')"
            />
          </div>
          <table class="ad__perf-table">
            <thead>
              <tr>
                <th scope="col">{{ t('feedback.dashboard.perf.col.route') }}</th>
                <th scope="col">{{ t('feedback.dashboard.perf.col.count') }}</th>
                <th scope="col">p50</th>
                <th scope="col">p95</th>
                <th scope="col">p99</th>
                <th scope="col">{{ t('feedback.dashboard.perf.col.errors') }}</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="row in visiblePerfRoutes" :key="`${row.method} ${row.route}`">
                <tr>
                  <!-- 方法 + 路由**模板**。模板里那个 `{id}` 要看得见：读者说「这条慢」
                       时，指的正是这个模板。状态码是**属性**不是身份，收在 errors 一列。
                       行首 chevron 展开这条路由的分钟级 spark；spark 全 null 的行没有
                       可展开的东西，chevron 禁用。 -->
                  <td class="ad__perf-where">
                    <button
                      type="button"
                      class="ad__perf-toggle"
                      :aria-expanded="expandedRoute === `${row.method} ${row.route}`"
                      :disabled="sparkDead(row.spark)"
                      :aria-label="`${row.method} ${row.route}`"
                      @click="toggleRoute(`${row.method} ${row.route}`)"
                    >
                      <span
                        class="mdi"
                        :class="
                          expandedRoute === `${row.method} ${row.route}` ? 'mdi-chevron-down' : 'mdi-chevron-right'
                        "
                        aria-hidden="true"
                      />
                    </button>
                    <span class="ad__perf-method">{{ row.method }}</span>
                    <span class="ad__perf-path num-leaf">{{ row.route }}</span>
                  </td>
                  <td class="t-num ad__perf-num">{{ row.count ? fmtNum(row.count) : '—' }}</td>
                  <td class="t-num ad__perf-num">{{ ms(row.p50) }}</td>
                  <!-- p95 内嵌一根微型量级条（按全表最大值归一）：竖着扫一眼就知道谁慢、
                       慢多少；超过 1s 的条点琥珀 —— 全页唯一的警示色份额用在「真的慢」上。 -->
                  <td class="t-num ad__perf-num ad__perf-p95">
                    <span class="ad__perf-p95cell">
                      <span class="ad__perf-p95bar" aria-hidden="true">
                        <span
                          class="ad__perf-p95fill"
                          :class="{ 'ad__perf-p95fill--hot': isHotP95(row.p95) }"
                          :style="{ width: p95BarWidth(row.p95) }"
                        />
                      </span>
                      {{ ms(row.p95) }}
                    </span>
                  </td>
                  <td class="t-num ad__perf-num">{{ ms(row.p99) }}</td>
                  <td class="t-num ad__perf-num">{{ row.error_count ? fmtNum(row.error_count) : '—' }}</td>
                </tr>
                <tr v-if="expandedRoute === `${row.method} ${row.route}`" class="ad__perf-detail">
                  <td colspan="6">
                    <p class="ad__perf-sparknote t-meta-read">{{ t('feedback.dashboard.perf.sparkTitle') }}</p>
                    <AdminSparkline :values="row.spark" :height="32" />
                  </td>
                </tr>
              </template>
              <tr v-if="filteredPerfRoutes.length === 0">
                <td colspan="6" class="ad__perf-empty t-meta-read">
                  {{ t('feedback.dashboard.perf.filterEmpty', { q: routeFilter.trim() }) }}
                </td>
              </tr>
            </tbody>
          </table>
          <!-- 折叠行本身就是结论：被折掉的那些 p95 最高也就这么多，不看也罢。
               路由再多（42 条、100 条）这一屏的高度都不再跟着长。 -->
          <button v-if="perfFold" type="button" class="ad__perf-fold" @click="perfFoldOpen = !perfFoldOpen">
            {{ perfFold }}
          </button>
          <!-- 常驻注（每屏至多一条的额度给了它）：进程内存、重启清零、只覆盖业务 API。 -->
          <p class="ad__perf-note t-meta">{{ t('feedback.dashboard.perf.note') }}</p>
        </div>

        <!-- 网络吞吐 + 投递积压两联。网络：两面都给，各有口径（见 `core/net_io.py`）。
             上行是**这台机器的网卡**（含计量代理到 LLM 的出向流量），api 是本进程的
             HTTP 载荷。读不到画「—」—— 0 会把「看不见」说成「网是闲的」。读数下面的
             迷你线是逐分钟样本的形状（响应里一直回、此前没人画）。投递：接口很快而
             投递发不出去时，用户什么都没收到，p95 还是绿的。 -->
        <div v-if="netUplink || netApi || reliability" class="ad__perf-duo">
          <section v-if="netUplink || netApi" class="ad__panel">
            <h2 class="ad__block-title">{{ t('feedback.dashboard.perf.network.title') }}</h2>
            <div class="ad__panel-grid">
              <div v-if="netUplink" class="ad__netcell">
                <span class="ad__cell-head">
                  <span class="t-eyebrow-read">{{ t('feedback.dashboard.perf.network.uplink') }}</span>
                  <AdminNoteTip
                    :text="t('feedback.dashboard.perf.network.uplinkNote', { iface: netUplink.iface ?? '—' })"
                  />
                </span>
                <span class="t-dense num-leaf" :title="netUplink.note_key">
                  ↓ {{ bps(netUplink.rx_bps) }} · ↑ {{ bps(netUplink.tx_bps) }}
                </span>
                <AdminSparkline :values="netUplink.samples.map((s) => s.rx_bps)" :height="32" />
              </div>
              <div v-if="netApi" class="ad__netcell">
                <span class="ad__cell-head">
                  <span class="t-eyebrow-read">{{ t('feedback.dashboard.perf.network.api') }}</span>
                  <AdminNoteTip :text="t('feedback.dashboard.perf.network.apiNote')" />
                </span>
                <span class="t-dense num-leaf" :title="netApi.note_key">
                  ↓ {{ bps(netApi.rx_bps) }} · ↑ {{ bps(netApi.tx_bps) }}
                </span>
                <AdminSparkline :values="netApi.samples.map((s) => s.rx_bps)" :height="32" />
              </div>
            </div>
          </section>

          <section v-if="reliability" class="ad__panel">
            <h2 class="ad__block-title">
              {{ t('feedback.dashboard.reliability.title')
              }}<AdminNoteTip :text="t('feedback.dashboard.reliability.note')" />
            </h2>
            <div class="ad__mini-grid">
              <div class="ad__mini">
                <span class="ad__mini-label t-eyebrow-read">{{
                  t('feedback.dashboard.integrations.delivery.unsent')
                }}</span>
                <span class="ad__mini-value t-num">{{ num(reliability.delivery_unsent) }}</span>
              </div>
              <div class="ad__mini">
                <span class="ad__mini-label t-eyebrow-read">{{
                  t('feedback.dashboard.integrations.delivery.dead')
                }}</span>
                <span class="ad__mini-value t-num">{{ num(reliability.delivery_dead_letters) }}</span>
              </div>
            </div>
          </section>
        </div>
      </template>

      <!-- 平台：账号的存量与新增、以及机器台账的存量。 -->
      <template v-else>
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in peopleKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
            :delta="kpi.delta"
            :delta-title="kpi.deltaTitle"
            :spark="kpi.spark"
          />
        </div>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.people.chart')"
            :x-labels="xLabels"
            :series="signupSeries"
            :loading="store.statsLoading"
          />

          <section class="ad__machines">
            <h2 class="ad__block-title">{{ t('feedback.dashboard.machines.title') }}</h2>
            <dl class="ad__machine-list">
              <template v-for="row in machines" :key="row.key">
                <dt class="t-meta-read">{{ row.label }}</dt>
                <dd class="t-num">{{ row.value }}</dd>
              </template>
            </dl>
            <!-- 常驻注（每屏至多一条的额度给了它）：这四行是存量，不是在线数。 -->
            <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.machines.note') }}</p>
          </section>
        </div>

        <!-- 健康度：**这一刻**的，和上面两组的「存量 / 窗口」不是一回事。状态色只在
             这一行用（up / stalling / down），全页别处都是中性阶。 -->
        <section v-if="healthRows.length" class="ad__health">
          <h2 class="ad__block-title">
            {{ t('feedback.dashboard.health.title') }}<AdminNoteTip :text="t('feedback.dashboard.health.note')" />
          </h2>
          <div class="ad__health-grid">
            <div v-for="row in healthRows" :key="row.key" class="ad__health-cell">
              <span class="ad__health-dot" :class="`ad__health-dot--${row.tone}`" aria-hidden="true" />
              <span class="ad__health-label t-eyebrow-read">{{ row.label }}</span>
              <span class="ad__health-status t-body">{{ row.status }}</span>
            </div>
          </div>
        </section>

        <!-- 配额与缺口：磁盘（只这台后端）/ 预览（进程内存）两条计量，加机器普查的
             两张状态分布（台账行与状态，不是容器数 —— 那句口径写在两张图各自的注里）。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.extras.title') }}</h2>
          <div class="ad__split-grid">
            <AdminMeterBar
              v-for="row in extrasRows"
              :key="row.label"
              :label="row.label"
              :value-text="row.valueText"
              :limit="row.limit"
              :ratio="row.ratio"
              :tone="row.tone"
              :hint="row.hint"
              :note="row.note"
              :loading="store.statsLoading"
            />
            <AdminShareBar
              v-if="warmSegments.length"
              :title="t('feedback.dashboard.extras.warmStates')"
              :segments="warmSegments"
              :note="t('feedback.dashboard.extras.machines.note')"
              :loading="store.statsLoading"
            />
            <AdminShareBar
              v-if="projectMachineSegments.length"
              :title="t('feedback.dashboard.extras.projectStates')"
              :segments="projectMachineSegments"
              :note="t('feedback.dashboard.extras.machines.note')"
              :loading="store.statsLoading"
            />
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 滚动归这一页自己领：外壳（`AdminLayout` 的 `.admin-shell__main`）只让高度和宽度，
   不给滚动（和 `AdminMembersPage` 同一套约定）。少了 `overflow-y: auto`，内容比一屏高
   时下半截会被外面那层 `overflow-hidden` 裁掉，而且没人能滚。 */
.ad {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 16px 24px 24px;
  overflow-y: auto;
}

/* 1440 那一列的水平居中（宽度走 `page-container--admin`，这里只管位置）。
   `container-type: inline-size`：下面所有断点都是**容器查询**（先例：
   `RunningWorkView`，理由相同 —— 侧栏能手折，折出来的 144px 视口媒体查询看不见，
   断点要看的是「这一格有多宽」）。 */
.ad__inner {
  display: flex;
  flex-direction: column;
  margin: 0 auto;
  container-type: inline-size;
}

/* 页首块：第一行「标题 + 窗口切换 + 时间戳」，第二行分类页签。 */
.ad__head {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  padding-top: 4px;
}

.ad__head-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.ad__head-side {
  display: flex;
  flex: 0 0 auto;
  align-items: baseline;
  gap: 16px;
}

/* 页头三件套（标题 / 窗口切换 / 时间戳）里最不重要的一个：窄屏让位（截断），
   不把页头撑出横向滚动。 */
.ad__stamp {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 切换控件只有一种语言：下划线小页签。窗口切换是小号版（12.5px），分类页签是
   正文版（13.5px）；激活 = 墨色 + 2px 下划线，未激活 = 灰。整页没有框状切换钮。 */
.ad__wintabs {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 16px;
}

.ad__wintab {
  position: relative;
  padding: 2px 1px 4px;
  background: none;
  border: 0;
  color: var(--faint);
  font-size: 12.5px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
}

.ad__wintab::after {
  position: absolute;
  right: 0;
  bottom: -2px;
  left: 0;
  height: 2px;
  background: var(--ink);
  opacity: 0;
  content: '';
}

.ad__wintab--on {
  color: var(--ink);
}

.ad__wintab--on::after {
  opacity: 1;
}

@media (hover: hover) and (pointer: fine) {
  .ad__wintab:not(.ad__wintab--on):hover {
    color: var(--text);
  }
}

.ad__wintab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 分类页签：贴着标题（14px），下沿用 `--line` 分隔内容与导航。窄了横向滚动
   （不换行 —— 换行会把页签堆成一面墙）。 */
.ad__kinds {
  display: flex;
  flex: 0 0 auto;
  gap: 28px;
  margin: 14px 0 0;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
}

/* 一颗页签：状态点 + 标签 + 短值一行。它是**按钮**（切分类），可访问名字保持
   裸标签（短值与点 `aria-hidden`，见模板注释）。 */
.ad__kind {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  padding: 0 2px 11px;
  background: none;
  border: 0;
  color: var(--muted);
  font-size: 13.5px;
  font-weight: 600;
  line-height: var(--lh-13);
  cursor: pointer;
}

.ad__kind::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: var(--ink);
  opacity: 0;
  content: '';
}

/* 选中态 = 墨色 + 下划线。**不用琥珀** —— 后台的琥珀份额已给侧栏选中条，一屏一处。 */
.ad__kind--on {
  color: var(--ink);
}

.ad__kind--on::after {
  opacity: 1;
}

@media (hover: hover) and (pointer: fine) {
  .ad__kind:not(.ad__kind--on):hover {
    color: var(--text);
  }
}

.ad__kind:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.ad__kind-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态点：6px 圆点，状态色 mark 档（真实状态，不是装饰）。 */
.ad__kind-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
}

.ad__kind-dot--ok {
  background: var(--ok);
}

.ad__kind-dot--warn {
  background: var(--warn);
}

.ad__kind-dot--danger {
  background: var(--danger);
}

/* 短值是附属读数：小两档（11.5px）、字色压一档，不跟标签抢；警示/健康/危险
   三档改色 —— 「这块需要我」的信号。 */
.ad__kind-val {
  overflow: hidden;
  color: var(--muted);
  font-size: 11.5px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__kind-val--warn {
  color: var(--warn-ink);
}

.ad__kind-val--ok {
  color: var(--ok-ink);
}

.ad__kind-val--danger {
  color: var(--danger-ink);
}

/* 「此刻最慢」横幅：左侧一道墨线，不是卡片 —— 它是性能屏的结论，不是又一块内容。 */
.ad__slowest {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
  padding: 12px 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 3px solid var(--ink);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
}

.ad__slowest-label {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.ad__slowest-route {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__slowest-val {
  flex: 0 0 auto;
  margin-left: auto;
  text-align: right;
}

.ad__slowest-num {
  display: block;
  color: var(--ink);
  font-size: 20px;
  font-weight: 650;
  line-height: var(--lh-15);
}

.ad__slowest-meta {
  display: block;
  color: var(--faint);
  font-size: 11.5px;
}

/* 性能那一类的路由表。它是一整块表而不是卡片：这一类的读法是竖着扫「哪一条 p95
   最高」，卡片一多就扫不动了。窄屏横滚（`overflow-x: auto` + 表格 min-width）——
   六列 12.5px 在 320px 里只会互相压，横滚是移动端一等场景下的体面降级。 */
.ad__perf {
  margin-top: 16px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
  overflow-x: auto;
}

.ad__perf-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

/* 「筛路由…」：找具体某条接口的入口，收在表头行右侧。 */
.ad__perf-filter {
  width: 160px;
  margin-left: auto;
  padding: 4px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12.5px;
  outline: none;
}

.ad__perf-filter::placeholder {
  color: var(--faint);
}

.ad__perf-filter:focus-visible {
  border-color: var(--focus-ring);
}

.ad__perf-table {
  width: 100%;
  min-width: 560px;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: var(--lh-12);
}

.ad__perf-table th,
.ad__perf-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}

.ad__perf-table th {
  font-weight: 600;
  color: var(--muted);
  text-align: left;
}

.ad__perf-table tr:last-child td {
  border-bottom: 0;
}

/* 数值列右对齐（含表头）：一列数字竖着看要对得上位。第一列是「接口」，不参与。 */
.ad__perf-table th:not(:first-child),
.ad__perf-table td:not(:first-child) {
  text-align: right;
}

.ad__perf-where {
  color: var(--muted);
}

/* 方法是大写英文、路由是模板，两者之间留一点气口；状态码再淡一档。 */
.ad__perf-method {
  margin-right: 6px;
  font-weight: 600;
  color: var(--ink);
}

.ad__perf-status {
  margin-left: 6px;
  color: var(--faint);
}

.ad__perf-num {
  color: var(--ink);
}

/* 展开 chevron：整行一个按钮。禁用态（spark 全 null）淡到 `--faint`，不藏 ——
   同一列的图标有有无无，比「有的行窄一截」好读。 */
.ad__perf-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  margin-right: 4px;
  color: var(--muted);
  vertical-align: middle;
  cursor: pointer;
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  transition: color 0.12s ease;
}

.ad__perf-toggle:hover:not(:disabled) {
  color: var(--text);
}

.ad__perf-toggle:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.ad__perf-toggle:disabled {
  color: var(--faint);
  cursor: default;
}

.ad__perf-toggle .mdi {
  font-size: 14px;
  line-height: 1;
}

/* 展开行：底色换一档标出「它属于上面那一行」。 */
.ad__perf-detail td {
  padding: 8px 16px 12px;
  background: var(--fill);
  text-align: left;
}

.ad__perf-sparknote {
  margin: 0 0 6px;
}

/* p95 列：数值右侧、微型量级条在左（先形后数）。格子给一点宽度下限，条才站得开。 */
.ad__perf-p95 {
  min-width: 120px;
}

.ad__perf-p95cell {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.ad__perf-p95bar {
  display: block;
  flex: 0 0 auto;
  width: 56px;
  height: 4px;
  overflow: hidden;
  background: var(--fill);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.ad__perf-p95fill {
  display: block;
  height: 100%;
  background: var(--ink);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

/* 「真的慢」（p95 ≥ 1s）点琥珀 —— 全页唯一的警示色份额给它。 */
.ad__perf-p95fill--hot {
  background: var(--warn);
}

/* 折叠行：整宽一个安静的按钮，文案本身就是结论（被折掉的 p95 上限）。 */
.ad__perf-fold {
  display: flex;
  width: 100%;
  justify-content: center;
  gap: 6px;
  margin-top: 4px;
  padding: 10px;
  background: none;
  border: 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
}

@media (hover: hover) and (pointer: fine) {
  .ad__perf-fold:hover {
    color: var(--text);
  }
}

.ad__perf-fold:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.ad__perf-empty {
  padding: 20px 8px;
  text-align: center;
}

.ad__perf-note {
  margin: 12px 0 0;
  line-height: var(--lh-12);
}

/* 性能屏底部两联：网络吞吐 + 投递积压，各是一块安静的面板。 */
.ad__perf-duo {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  margin-top: 20px;
}

@container (min-width: 720px) {
  .ad__perf-duo {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}

.ad__panel {
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.ad__panel-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.ad__netcell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}

/* 投递积压的两格小数字：label 在上、20px 数在下，格子间不画框。 */
.ad__mini-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.ad__mini {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}

.ad__mini-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__mini-value {
  color: var(--ink);
  font-size: 20px;
  line-height: var(--lh-15);
}

/* KPI 网格：N 张卡合成**一条整面板**（一个外框 + 内部分隔线，卡片自己的边框
   与写死高度在 `.ad__inner` 作用域内关掉，见 AdminKpiCard 的对应块）。边框数量
   从 N 个变 1 个，行高对齐是天生的 —— 不再需要 92/108px 那档妥协。
   窄 2 列 → ≥560 4 列 → ≥1320 auto-fit（4–6 列，卡数不一也不留空轨）。
   断点是**容器查询**（挂 `.ad__inner`），理由见 `.ad__inner`。 */
.ad__kpis {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin-top: 16px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

@container (min-width: 560px) {
  .ad__kpis {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

/* ≥1320 用 auto-fit 不写死 6 列：各类卡数不同（交付/平台 5、其余 4），auto-fit 让
   4 卡的类自动 4 等分、5–6 卡的类铺满，不留空轨。 */
@container (min-width: 1320px) {
  .ad__kpis {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

/* 格子换成「缝」：gap 0，分隔线用每格自己的上边线 + 左边线、外边框裁掉第一行/列
   （overflow: hidden + 负 margin，第一行的上边框与第一列的左边框被推出面板外）。 */
.ad__kpis > :deep(*) {
  margin-top: -1px;
  margin-left: -1px;
  border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
}

.ad__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

/* 两块并排要各到 ~300px 以上，图里的刻度才不互相压 —— 所以双栏从 720 容器宽开始。 */
@container (min-width: 720px) {
  .ad__row {
    grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  }

  .ad__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}

.ad__none {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 48px 0 0;
}

.ad__none-title {
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}

.ad__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 错误块里「重试」是唯一主操作 —— 琥珀份额归它（design-system §1.6）。 */
.ad__retry {
  align-self: flex-start;
  margin-top: 8px;
}

/* 急件警示行。只有真的压着没人管的急件时才画，所以它一出现就该被看见 —— 用
   `--warn-ink` 的文字而不是整块琥珀底：琥珀在这套设计系统里只留给「当前唯一的主操作」
   （design-system §1.6），一个警示行不是操作。 */
.ad__urgent {
  margin: 12px 0 0;
  color: var(--warn-ink);
  line-height: var(--lh-12);
}

/* 四栏计数。四格并排，和 KPI 行同一套格子，但高度矮一档 —— 它们是同一个总数的四个
   筛选视角，不该和「四个各自独立的数」争同一档视觉重量。 */
.ad__split {
  margin-top: 20px;
}

.ad__split-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

@container (min-width: 560px) {
  .ad__split-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container (min-width: 1320px) {
  .ad__split-grid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

.ad__split-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.ad__split-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__split-value {
  color: var(--ink);
  font-size: 20px;
}

/* 格内 label + 口径 tip 同一行（网络吞吐两格）。 */
.ad__cell-head {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ad__cell-note {
  color: var(--muted);
}

/* 健康度。**状态色只在这一块用**（up / stalling / down），全页别处都是中性阶：一屏里
   只有一处有颜色的时候，那一处就是「需要看的地方」。 */
.ad__health {
  margin-top: 20px;
}

.ad__health-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}

@container (min-width: 720px) {
  .ad__health-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.ad__health-cell {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.ad__health-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}

.ad__health-dot--ok {
  background: var(--ok);
}

.ad__health-dot--warn {
  background: var(--warn);
}

.ad__health-dot--danger {
  background: var(--danger);
}

.ad__health-label {
  flex: 0 0 auto;
}

.ad__health-status {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__machines {
  display: flex;
  flex-direction: column;
}

/* 块标题：标题 + （可选的）口径 tip 同一行。 */
.ad__block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}

/* 机器那四行。两列：名字在左、数在右 —— 这一块在读四个并列的量，竖着排成
   「名字 / 数 / 名字 / 数」会让四个数对不齐。 */
.ad__machine-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px 16px;
  margin: 0;
}

.ad__machine-list dt,
.ad__machine-list dd {
  min-width: 0;
  overflow-wrap: anywhere;
}

.ad__machine-list dd {
  margin: 0;
  text-align: right;
  color: var(--ink);
}

.ad__block-note {
  margin: 12px 0 0;
}
</style>
