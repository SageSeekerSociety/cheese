<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { StatsKind } from '@/api'
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminBreakTable from '@/components/admin/AdminBreakTable.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminLiveSpine from '@/components/admin/AdminLiveSpine.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import { fmtCost, fmtNum } from '@/lib/usageFormat'
import { useFeedbackStore } from '@/stores/feedback'

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
//     合成一个数就会有一个是错的。
//   * **未定价的 token 单独算**。订阅按月计费，那些行上的 `cost_usd = 0.0` 意思是
//     「没有单价」而不是「免费」；把它们加进总额，会在几百万 token 上印一个
//     `$0.0000`，读起来像「这个月没花钱」。
//
// 墙上每个数字都写得出点下去去哪（§6.3 那张准入表）。机器那四个数是例外，而且是**说
// 得出理由的例外**：平台里没有一页列设备，它们点不进去，所以那一块只报数、不装成链接
// （口径是存量，见 `machines`）。
defineOptions({ name: 'AdminDashboardPage' })

const store = useFeedbackStore()
const { t } = useI18n()
const router = useRouter()

/** 页头上那句「过去 7 天」问的窗口。窗口是**页面**的问题（`loadStats` 收参数）。 */
const DAYS = 7

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

/** 顶上那条「四块摘要」—— 这一页的**第一眼**。
 *
 *  分类控件把四块藏在四个抽屉里，于是「反馈在催、用量在涨」这件事要么点三下才看见，
 *  要么根本看不见。这一条把每一块的那**一个**数摆在一起：读的人先知道「现在哪块
 *  需要我」，再决定进哪一块。
 *
 *  每块只取一个数（不是四个）：一行摆四个指标 × 四块 = 十六个数，那就不是摘要而是
 *  另一张表。取哪一个，判据是「这一块现在最该被看见的那件事」：
 *
 *   * 反馈 → **待分诊**（有事等着人动）；有急件时换成急件数并染琥珀警示。
 *   * 用量 → 窗口内的 **token**（量级，比钱稳 —— 未定价的那部分没有钱数）。
 *   * 平台 → **健康度**（`healthy` / `degraded`，这一刻的）。
 *   * 性能 → **最慢那条的 p95**（「哪条慢」是这一块唯一要回答的问题）。
 *
 *  点一块就切到那一类 —— 它是导航，不是卡片（`to` 语义上的链接由下面的 KPI 卡承担）。
 */
/** 摘要条的**短值**。
 *
 *  两个约束，都是从「两排卡片长一样」那次反馈来的：
 *
 *   1. **不重复下面 KPI 行的那一个数**。反馈那块的 KPI 第一张就是「待分诊」、用量
 *      第一张就是「窗口内 token」，摘要条再打一遍就是同一件事说两遍。所以摘要条
 *      给的是**这一块现在最该被看见的那件事**的**短语**（「3 急件」「20 万」），
 *      而不是和 KPI 同一个数字。
 *   2. **短语，不是大数字**。大数字留给下面那排 KPI 卡；摘要条是导航，它的字是
 *      `.t-dense`，不是 `.t-console-title`。
 */
const pulse = computed(() => {
  const rows = [
    {
      key: 'pipeline',
      label: t('feedback.dashboard.tab.pipeline'),
      // 「等你」是这一块唯一要回答的问题，而且它**不在**下面的 KPI 第一张
      // （第一张是「还在走」）。
      value: t('feedback.dashboard.pipeline.kpi.needsYouShort', {
        n:
          (pipeline.value?.needs_you.reviewer_pending ?? 0) +
          (pipeline.value?.needs_you.open_tasks ?? 0) +
          (pipeline.value?.needs_you.awaiting_answer ?? 0),
      }),
      hint: t('feedback.dashboard.pipeline.needs.title'),
      tone: 'ink',
    },
    {
      key: 'product',
      label: t('feedback.dashboard.tab.product'),
      value: t('feedback.dashboard.product.northStarShort', {
        n: product.value?.north_star.total ?? 0,
      }),
      hint: t('feedback.dashboard.product.northStar'),
      tone: 'ink',
    },
    {
      key: 'feedback',
      label: t('feedback.dashboard.tab.feedback'),
      value:
        urgentOpen.value > 0
          ? t('feedback.dashboard.kpi.urgentShort', { n: urgentOpen.value })
          : t('feedback.dashboard.kpi.untriagedShort', {
              n: feedback.value?.total.unassigned ?? 0,
            }),
      hint: urgentOpen.value > 0 ? t('feedback.dashboard.kpi.urgent') : t('feedback.dashboard.kpi.untriaged'),
      tone: urgentOpen.value > 0 ? 'warn' : 'ink',
    },
    {
      key: 'usage',
      label: t('feedback.dashboard.tab.usage'),
      value: shortTokens(usage.value?.totals.tokens),
      hint: t('feedback.dashboard.usage.tokens'),
      tone: 'ink',
    },
    {
      key: 'platform',
      label: t('feedback.dashboard.tab.platform'),
      value:
        platform.value?.health?.overall === 'healthy'
          ? t('feedback.dashboard.health.healthy')
          : platform.value?.health?.overall === 'degraded'
            ? t('feedback.dashboard.health.degraded')
            : '—',
      hint: t('feedback.dashboard.health.title'),
      tone:
        platform.value?.health?.overall === 'healthy'
          ? 'ok'
          : platform.value?.health?.overall === 'degraded'
            ? 'danger'
            : 'ink',
    },
    {
      key: 'performance',
      label: t('feedback.dashboard.tab.performance'),
      value: slowestP95.value,
      hint: t('feedback.dashboard.perf.slowest'),
      tone: 'ink',
    },
    {
      key: 'integrations',
      label: t('feedback.dashboard.tab.integrations'),
      value: t('feedback.dashboard.integrations.delivery.deadShort', {
        n: integrations.value?.delivery.dead_letters ?? 0,
      }),
      hint: t('feedback.dashboard.integrations.delivery.title'),
      tone: (integrations.value?.delivery.dead_letters ?? 0) > 0 ? 'warn' : 'ink',
    },
  ]
  return rows
})

/** 一键一格，给上面那条导轨按 `StatsKind` 取短值用。`pulse` 仍是数组（渲染顺序），
 *  这里只是同一批数据的按名索引。 */
const pulseByKey = computed<Record<string, (typeof pulse.value)[number]>>(() => {
  const out: Record<string, (typeof pulse.value)[number]> = {}
  for (const row of pulse.value) out[row.key] = row
  return out
})

/** 20 万 token 这种短写 —— 摘要条上摆 `204,900` 是把下面 KPI 的同一个数再念一遍。 */
function shortTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${Math.round(n / 1000)}k`
  return String(n)
}

/** 最慢那条路由的 p95 —— 「哪条慢」是性能那一块唯一要回答的问题。 */
const slowestP95 = computed(() => {
  const row = perf.value?.routes?.[0]
  return row?.p95 === undefined || row.p95 === null ? '—' : `${row.p95} ms`
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
 *  切回来看到的是刚才那份，而「重新拉一次」是刷新页面的事。 */
function selectKind(next: StatsKind) {
  if (next === store.statsKind) return
  store.statsKind = next
  if (store.stats[next] === null) void store.loadStats(next, DAYS)
}

function sumOf(values: number[] | undefined): string {
  return values ? fmtNum(values.reduce((acc, n) => acc + n, 0)) : ''
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
 *  和「进行中」在卡片上和图上是两个数，两边各自都看着对。 */
const feedbackKpis = computed(() => [
  {
    key: 'untriaged',
    label: t('feedback.dashboard.kpi.untriaged'),
    value: num(feedback.value?.total.unassigned),
    loading: store.statsLoading,
    to: queue({ assigned: 'none' }),
  },
  {
    key: 'inProgress',
    label: t('feedback.dashboard.kpi.inProgress'),
    value: num(feedback.value?.total.open),
    loading: store.statsLoading,
    to: queue({ status: 'in_progress' }),
  },
  {
    key: 'created7d',
    label: t('feedback.dashboard.kpi.created7d'),
    value: sumOf(feedback.value?.series.map((row) => row.created)),
    loading: store.statsLoading,
    to: queue({ since: '7d' }),
  },
  {
    key: 'resolved7d',
    label: t('feedback.dashboard.kpi.resolved7d'),
    value: sumOf(feedback.value?.series.map((row) => row.resolved)),
    loading: store.statsLoading,
    to: queue({ resolved_since: '7d' }),
  },
])

/** 队列那四栏的计数 —— **筛选，不是划分**（`agent` 是来源，和公开/私密重叠），所以
 *  四个数加起来不等于总数。这一行要说清这件事，否则「四个数对不上」会被读成算错了。 */
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

const usageKpis = computed(() => [
  {
    key: 'tokens',
    label: t('feedback.dashboard.usage.tokens'),
    value: num(usage.value?.totals.tokens),
    loading: store.statsLoading,
    to: queue(),
  },
  {
    key: 'calls',
    label: t('feedback.dashboard.usage.calls'),
    value: num(usage.value?.totals.calls),
    loading: store.statsLoading,
    to: queue(),
  },
  /* 成本和「算不出价钱的 token」是两张卡，不是页脚的一行字。两件事各自是一个数，而
     「一行正文 + 一行脚注」那种写法把它们降级成了注释 —— 和左边那样的四张卡对齐之后，
     这一类的 KPI 行才和反馈那一类长得一样。两张都不带 `to`：队列没有按钱筛的口径。 */
  {
    key: 'cost',
    label: t('feedback.dashboard.cost.kpi'),
    value: usage.value ? fmtCost(usage.value.totals.cost_usd) : '',
    loading: store.statsLoading,
  },
  {
    key: 'unpriced',
    label: t('feedback.dashboard.cost.unpricedLabel'),
    value: num(usage.value?.totals.unpriced_tokens),
    loading: store.statsLoading,
  },
])

/** 窗口内每天的 token。**只画 token 一条**：调用次数和钱各自有不同的量级，三条线画在
 *  一根轴上会有两条贴着底走 —— 而「贴着底的那条是不是 0」正是这张图要回答的问题。 */
const usageSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.usage.tokens'),
    values: usage.value?.series.map((row) => row.tokens) ?? [],
    style: 'solid',
  },
])

/** 用量最高的几个项目。条只报 token —— 同一根条上再叠一个「花了多少钱」，它们的长度
 *  就各自代表不同的东西，而长短本来是用来比的。项目名原样给组件（它自己省略号）。 */
const topProjects = computed(() =>
  (usage.value?.top_projects ?? []).map((row) => ({ label: row.name, value: row.tokens }))
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

/** 成本那一行下面那句口径。两个数（金额、没有单价的 token）各自已经是 KPI 卡了，
 *  这句话说的是**它们之间的关系** —— 订阅按月计费，那些行上的 `cost_usd = 0.0` 意思是
 *  「没有单价」而不是「免费」，所以金额里没有它们，也不该被读成「这个月没花钱」。
 *  一行字，跟着那两张卡走；`usage` 还没到时不画。 */
const costNote = computed(() => (usage.value ? t('feedback.dashboard.cost.note') : ''))

/* ---- 性能那一块 ----
 *
 * 它是四类里唯一**读进程内存**的（另外三类读库），所以页面上必须把那三件口径说
 * 出来：没有窗口（只有此刻）、重启即清零、只覆盖业务 API。不说的话，读者会把它当
 * 成「整个平台的、有历史的」数 —— 而它两个都不是。
 */

/* ---- 交付管线（新） ---------------------------------------------------- */

const pipeline = computed(() => store.stats.pipeline)

/** 活四站：建卡 → 决议 → 合并 → 归档。**没有「闸门」那一站** —— 机器闸门已退役
 *  （#296），画上去就是一个永远空的站。四站各是**不同卡在不同时刻**的通过量，不是
 *  同一批样本的漏斗，所以 `AdminLiveSpine` 用导轨不用漏斗图。 */
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
    },
    {
      key: 'decided',
      label: t(STATION_KEY.decided),
      count: p.dwell.filed_to_decision.count,
      dwellSeconds: p.dwell.filed_to_decision.p50_seconds,
    },
    {
      key: 'merged',
      label: t(STATION_KEY.merged),
      count: merged,
      dwellSeconds: p.dwell.filed_to_merge.p50_seconds,
    },
    {
      key: 'archived',
      label: t(STATION_KEY.archived),
      count: archived,
      dwellSeconds: p.dwell.accepted_not_archived.max_seconds,
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

const pipelineKpis = computed(() => {
  const p = pipeline.value
  return [
    {
      key: 'live',
      label: t('feedback.dashboard.pipeline.kpi.live'),
      value: num(p?.backlog.live_total),
      loading: store.statsLoading,
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

const hostRows = computed(() =>
  (pipeline.value?.host_health.rows ?? []).map((row) => ({
    id: row.device_id,
    title: row.device_id,
    subtitle: row.last_failure_code ?? '',
    statusLabel: String(row.consecutive_failures),
    tone: row.quarantined_until ? ('danger' as const) : ('warn' as const),
    to: { path: '/admin' },
  }))
)

/* ---- 产品健康（新） ---------------------------------------------------- */

const product = computed(() => store.stats.product)

const productKpis = computed(() => [
  {
    key: 'north',
    label: t('feedback.dashboard.product.northStar'),
    value: num(product.value?.north_star.total),
    loading: store.statsLoading,
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
    name: t('feedback.dashboard.product.northStar'),
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

const integrationKpis = computed(() => [
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
  }[] = []
  if (x.disk.available && x.disk.used_pct !== undefined) {
    rows.push({
      label: t('feedback.dashboard.extras.disk.title'),
      valueText: `${x.disk.used_pct}%`,
      limit: 100,
      ratio: x.disk.used_pct / 100,
      tone: x.disk.tier === 'critical' ? 'danger' : x.disk.tier === 'warn' ? 'warn' : 'ink',
      hint: `${x.disk.free_gb} GB`,
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
    })
  }
  return rows
})

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
 *  `null` 是「这一格没有数据」。 */
const ms = (v: number | null | undefined): string => (v === null || v === undefined ? '—' : `${v} ms`)

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
  if (registered === undefined || registered === null) {
    return p.routes_total > p.routes_shown
      ? t('feedback.dashboard.perf.routesTruncated', { shown: p.routes_shown, total: p.routes_total })
      : String(p.routes_total)
  }
  return t('feedback.dashboard.perf.routesOf', { shown: p.routes_total, total: registered })
})

const lagText = computed(() => {
  const lag = perf.value?.loop_lag
  return lag === undefined ? '' : `${lag.recent_ms} ms`
})

/* ---- 平台那一块 ---- */

const peopleKpis = computed(() => [
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
    label: t('feedback.dashboard.people.new'),
    value: num(platform.value?.people.new),
    loading: store.statsLoading,
  },
  {
    key: 'admins',
    label: t('feedback.dashboard.people.admins'),
    value: num(platform.value?.people.admins),
    loading: store.statsLoading,
  },
])

const signupSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.people.new'),
    values: platform.value?.people.series.map((row) => row.created) ?? [],
    style: 'solid',
  },
])

/** 机器那四行。**存量，不是在线数** —— 在线状态住在进程内存里，库里没有可以查的那一
 *  列（`platform_stats/repositories.py` 的模块 docstring）。这句话必须写在页面上：一个
 *  「机器 5」的数字，读的人默认会当成「现在有 5 台在跑」。 */
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

/** 日期轴的标签。三类各有一条 series，长度都是 `days`。 */
const xLabels = computed(() => {
  const series =
    kind.value === 'platform'
      ? platform.value?.people.series
      : kind.value === 'usage'
        ? usage.value?.series
        : feedback.value?.series
  return (series ?? []).map((row) => dayLabel(row.date))
})

/** 轴标签写「9/15」：轴上七个点，写全年月日是七串数字挤在一起，而窗口在页头上已经
 *  说了是过去七天。 */
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

onMounted(() => {
  // 默认落点是**交付**：管理员早上第一个问题是「现在该我动的是哪几件」，不是
  // 「今天 token 多少」。所以这一页先回答它，再让运维那几块做诊断抽屉。
  if (store.statsKind === 'feedback') selectKind('pipeline')
  // 两件事并发：队列那一路给 `counts` 和迷你列表的十行（反馈分类要），看板那一路给当前
  // 分类的曲线。串行只会让首屏多等一个来回。
  void store.loadAdmin()
  if (store.stats[store.statsKind] === null) void store.loadStats(store.statsKind, DAYS)
})
</script>

<template>
  <div class="ad">
    <div class="ad__inner page-container--wide">
      <header class="ad__head">
        <h1 class="t-console-title">{{ t('feedback.dashboard.title') }}</h1>
        <span class="ad__window t-meta-read">{{ t('feedback.dashboard.window') }}</span>
      </header>

      <!-- 分类控件是这一页的**第一个控件**，也是**唯一**一条目的地导轨：读的人先决定
           看哪一类，再看数字。`v-btn-toggle` 而不是 tabs —— tabs 底下那条线会跟页头
           那条 `--line-2` 抢同一种「这里是边界」的意思，而分类不是边界，是一次筛选。

           **两行并成一行**（管理员指着两排问过「这两行是同一个东西」）。原本下面还
           有一条摘要条 `.ad__pulse`，和这里同一批键、同一批标签、同一个 `selectKind`，
           只多带一个短值 —— 于是每个目的地在页面上出现两次。现在短值就长在这一行里，
           摘要条整条删除。

           短值是**附属读数**，不是这个按钮的可访问名字：`aria-hidden` 掉它，按钮的
           accessible name 保持裸标签（`交付` / `用量` …）。否则 e2e 里
           `getByRole('button', { name: '反馈', exact: true })` 会因为名字变成
           「反馈 待分诊 3」而永远匹配不上。提示句放在 `title` 上，够指针用户读。 -->
      <div class="ad__kinds">
        <v-btn-toggle
          :model-value="store.statsKind"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
          @update:model-value="selectKind($event as StatsKind)"
        >
          <v-btn v-for="k in KINDS" :key="k" :value="k" size="small" :title="pulseByKey[k]?.hint">
            {{ t(TAB_KEY[k]) }}
            <span
              v-if="pulseByKey[k]"
              class="ad__kinds-val t-dense t-num"
              :class="`ad__kinds-val--${pulseByKey[k]!.tone}`"
              aria-hidden="true"
              >{{ pulseByKey[k]!.value }}</span
            >
          </v-btn>
        </v-btn-toggle>
      </div>

      <!-- 错误是**整块**的（§9.3）：这一页的主文案只有这一句，页头留着 —— 它是这一页
           的名字，不是数据。 -->
      <p v-if="failed" class="ad__none">
        <span class="ad__none-title">{{ t('feedback.dashboard.error.title') }}</span>
        <span class="ad__none-desc">{{ t('feedback.dashboard.error.desc') }}</span>
      </p>

      <!-- 交付管线：产品自己的主链。默认落点 —— 「现在该我动的是哪几件」排第一。 -->
      <template v-else-if="kind === 'pipeline'">
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in pipelineKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
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
          />
          <AdminActionList
            :title="t('feedback.dashboard.pipeline.stuck.title')"
            :rows="stuckRows"
            :loading="store.statsLoading"
            :empty="t('feedback.dashboard.pipeline.stuck.empty')"
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
          />
        </div>

        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.pipeline.title') }}</p>
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.pipeline.backlog.note') }}</p>
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.pipeline.stuck.note') }}</p>
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.pipeline.needs.note') }}</p>
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.pipeline.host.note') }}</p>
      </template>

      <!-- 产品健康：北极星 + 两条护栏 + 两条「今天算不出来」。 -->
      <template v-else-if="kind === 'product'">
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.product.title') }}</p>
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in productKpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="kpi.loading"
          />
        </div>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.product.northStar')"
            :x-labels="xLabels"
            :series="northSeries"
            :loading="store.statsLoading"
          />
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.product.northStarNote') }}</p>
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
          <h2 class="ad__block-title">{{ t('feedback.dashboard.product.unavailable.title') }}</h2>
          <p v-for="row in productUnavailable" :key="row.name" class="ad__none-desc t-meta-read">
            {{ row.text }}
          </p>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.product.unavailable.note') }}</p>
        </section>
      </template>

      <!-- 集成健康：静默降级。**没有 days** —— 凭据与投递是存量问题。 -->
      <template v-else-if="kind === 'integrations'">
        <p class="ad__cost-note t-meta">{{ t('feedback.dashboard.integrations.title') }}</p>
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
            :loading="store.statsLoading"
          />
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.integrations.passkey.note') }}</p>
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

        <section class="ad__split">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.integrations.unavailable.title') }}</h2>
          <p v-for="row in integrationsUnavailable" :key="row.name" class="ad__none-desc t-meta-read">
            {{ row.text }}
          </p>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.integrations.unavailable.note') }}</p>
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
          />
        </div>

        <!-- 急件警示：只有真的压着没人管的急件时才画。空着时不占位置。 -->
        <p v-if="urgentOpen > 0" class="ad__urgent t-meta">
          {{ t('feedback.dashboard.kpi.urgent', { n: urgentOpen }) }}
        </p>

        <!-- 四栏计数。**筛选不是划分**，所以行末那句口径必须在。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.column.title') }}</h2>
          <div class="ad__split-grid">
            <div v-for="col in feedbackColumns" :key="col.key" class="ad__split-cell">
              <span class="ad__split-label t-eyebrow-read">{{ col.label }}</span>
              <span class="ad__split-value t-console-title t-num">{{ col.value }}</span>
            </div>
          </div>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.column.note') }}</p>
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
            :to="kpi.to"
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

        <!-- 成本与「未定价 token」这两件事已经是上面那两张 KPI 卡了，这里只剩**它们
             之间的关系**那一句：金额里没有「算不出价钱」的那部分。以前它们挤在页脚一行
             里（一行正文加一行脚注），两个数被降级成了注释。 -->
        <p v-if="costNote" class="ad__cost-note t-meta">{{ costNote }}</p>

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
          <h2 class="ad__block-title">{{ t('feedback.dashboard.credits.title') }}</h2>
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
          <p v-if="credits" class="ad__block-note t-meta-read">
            {{ t('feedback.dashboard.credits.unlimited') }} {{ credits.unlimited_count }} ·
            {{ t('feedback.dashboard.credits.burn') }} {{ credits.burn.credits_per_day.toFixed(1) }}/d ·
            {{ t('feedback.dashboard.credits.eta') }} —
          </p>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.credits.note') }}</p>
        </section>
      </template>

      <!-- 性能：**这一刻**的接口耗时。它是四类里唯一读进程内存的，所以底下那句口径
           不是装饰 —— 少了它，这些数会被读成「有历史的、整个平台的」。 -->
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

        <div class="ad__perf">
          <table class="ad__perf-table">
            <thead>
              <tr>
                <th scope="col">{{ t('feedback.dashboard.perf.col.route') }}</th>
                <th scope="col">{{ t('feedback.dashboard.perf.col.count') }}</th>
                <th scope="col">p50</th>
                <th scope="col">p95</th>
                <th scope="col">p99</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in perfRoutes" :key="`${row.method} ${row.route} ${row.status}`">
                <!-- 方法 + 路由**模板** + 状态码。模板里那个 `{id}` 要看得见：读者
                     说「这条慢」时，指的正是这个模板。 -->
                <td class="ad__perf-where">
                  <span class="ad__perf-method">{{ row.method }}</span>
                  <span class="ad__perf-path">{{ row.route }}</span>
                  <span class="t-num ad__perf-status">{{ row.status }}</span>
                </td>
                <td class="t-num ad__perf-num">{{ fmtNum(row.count) }}</td>
                <td class="t-num ad__perf-num">{{ ms(row.p50) }}</td>
                <td class="t-num ad__perf-num">{{ ms(row.p95) }}</td>
                <td class="t-num ad__perf-num">{{ ms(row.p99) }}</td>
              </tr>
            </tbody>
          </table>
          <p class="ad__perf-note t-meta">{{ t('feedback.dashboard.perf.note') }}</p>
          <p class="ad__perf-note t-meta-read">{{ t('feedback.dashboard.perf.routesNote') }}</p>
        </div>

        <!-- 投递与事件积压：接口很快而投递发不出去时，用户什么都没收到，p95 还是绿的。 -->
        <section v-if="reliability" class="ad__split">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.reliability.title') }}</h2>
          <div class="ad__split-grid">
            <div class="ad__split-cell">
              <span class="ad__split-label t-eyebrow-read">{{
                t('feedback.dashboard.integrations.delivery.unsent')
              }}</span>
              <span class="ad__split-value t-console-title t-num">{{ num(reliability.delivery_unsent) }}</span>
            </div>
            <div class="ad__split-cell">
              <span class="ad__split-label t-eyebrow-read">{{
                t('feedback.dashboard.integrations.delivery.dead')
              }}</span>
              <span class="ad__split-value t-console-title t-num">{{ num(reliability.delivery_dead_letters) }}</span>
            </div>
            <div class="ad__split-cell">
              <span class="ad__split-label t-eyebrow-read">{{ t('feedback.dashboard.reliability.title') }}</span>
              <span class="ad__split-value t-console-title t-num">{{ num(reliability.spool?.unread) }}</span>
            </div>
          </div>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.reliability.note') }}</p>
        </section>
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
            <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.machines.note') }}</p>
          </section>
        </div>

        <!-- 健康度：**这一刻**的，和上面两组的「存量 / 窗口」不是一回事。状态色只在
             这一行用（up / stalling / down），全页别处都是中性阶。 -->
        <section v-if="healthRows.length" class="ad__health">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.health.title') }}</h2>
          <div class="ad__health-grid">
            <div v-for="row in healthRows" :key="row.key" class="ad__health-cell">
              <span class="ad__health-dot" :class="`ad__health-dot--${row.tone}`" aria-hidden="true" />
              <span class="ad__health-label t-eyebrow-read">{{ row.label }}</span>
              <span class="ad__health-status t-body">{{ row.status }}</span>
            </div>
          </div>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.health.note') }}</p>
        </section>

        <!-- 三样缺口：磁盘（只这台后端）/ 预览（进程内存）/ 机器普查（台账行不是容器）。 -->
        <section class="ad__split">
          <h2 class="ad__block-title">{{ t('feedback.dashboard.extras.disk.title') }}</h2>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.extras.machines.title') }}</p>
          <AdminMeterBar
            v-for="row in extrasRows"
            :key="row.label"
            :label="row.label"
            :value-text="row.valueText"
            :limit="row.limit"
            :ratio="row.ratio"
            :tone="row.tone"
            :hint="row.hint"
            :loading="store.statsLoading"
          />
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.extras.disk.note') }}</p>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.extras.preview.note') }}</p>
          <p class="ad__block-note t-meta-read">{{ t('feedback.dashboard.extras.machines.note') }}</p>
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

/* 1100 那一列的水平居中。宽度走 `page-container--wide`，这里只管位置。 */
.ad__inner {
  display: flex;
  flex-direction: column;
  margin: 0 auto;
}

/* 页头 56px，下沿用 `--line-2`（§4.2）。 */
.ad__head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  border-bottom: 1px solid var(--line-2);
}

.ad__window {
  flex: 0 0 auto;
}

.ad__kinds {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  margin: 16px 0 0;
}

/* 性能那一类的路由表。它是一整块表而不是卡片：这一类的读法是竖着扫「哪一条 p95
   最高」，卡片一多就扫不动了。 */
.ad__perf {
  margin-top: 16px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.ad__perf-table {
  width: 100%;
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

.ad__perf-note {
  margin: 12px 0 0;
  line-height: var(--lh-12);
}

.ad__kpis {
  display: grid;
  /* 四列等宽，跟着容器走 —— 卡片自己写死了高度，宽度由格子给。 */
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

/* 平台那一类只有三张卡：写死四列的话第四格是空的，看起来像少了一张。 */
.ad__kpis:has(> :nth-child(3):last-child) {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

/* 窄屏：一排四张挤不下，退成两排两列。再窄下去（一格不到 140px）文字开始被压。 */
@media (max-width: 900px) {
  .ad__kpis,
  .ad__kpis:has(> :nth-child(3):last-child) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.ad__row {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

/* 窄屏成一列：两块并排之后各自都不到 300px，图里的刻度会互相压。 */
@media (max-width: 1100px) {
  .ad__row {
    grid-template-columns: minmax(0, 1fr);
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

/* 成本那句口径。整行、缩进与上面那两张卡对齐，字号是元信息那一档 —— 它是一条
   注解，不是第三个数。 */
.ad__cost-note {
  margin: 12px 0 0;
  line-height: var(--lh-12);
}

/* 急件警示行。只有真的压着没人管的急件时才画，所以它一出现就该被看见 —— 用
   `--warn-ink` 的文字而不是整块琥珀底：琥珀在这套设计系统里只留给「当前唯一的主操作」
   （§0），一个警示行不是操作。 */
.ad__urgent {
  margin: 12px 0 0;
  color: var(--warn-ink);
  line-height: var(--lh-12);
}

/* 分类导轨里那个短值 —— 摘要条并进来之后的归宿。它是附属读数，所以字色压一档、
   不跟标签抢注意力；只有警示/健康/危险三档会改色，其余用 `--muted`。 */
.ad__kinds-val {
  margin-left: 6px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.ad__kinds-val--warn {
  color: var(--warn-ink);
}

.ad__kinds-val--ok {
  color: var(--ok-ink, var(--muted));
}

.ad__kinds-val--danger {
  color: var(--danger-ink, var(--warn-ink));
}

/* 四栏计数。四格并排，和 KPI 行同一套格子，但高度矮一档 —— 它们是同一个总数的四个
   筛选视角，不该和「四个各自独立的数」争同一档视觉重量。 */
.ad__split {
  margin-top: 20px;
}

.ad__split-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 900px) {
  .ad__split-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
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

/* 健康度。**状态色只在这一块用**（up / stalling / down），全页别处都是中性阶：一屏里
   只有一处有颜色的时候，那一处就是「需要看的地方」。 */
.ad__health {
  margin-top: 20px;
}

.ad__health-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 900px) {
  .ad__health-grid {
    grid-template-columns: repeat(1, minmax(0, 1fr));
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

.ad__row--equal {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}

.ad__machines {
  display: flex;
  flex-direction: column;
}

.ad__block-title {
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
