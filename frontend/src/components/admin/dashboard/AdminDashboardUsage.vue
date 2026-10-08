<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { KpiRow, StatsDays, StatsUsage } from '@/lib/adminStats'
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminBreakTable from '@/components/admin/AdminBreakTable.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import { poolView } from '@/lib/adminPool'
import { dayLabel, deltaOf, num } from '@/lib/adminStats'
import { fmtCost, fmtNum } from '@/lib/usageFormat'

// 用量那一屏：窗口内的 token 与调用次数、每天一条线、最花 token 的几个项目、成本那一
// 行，外加额度燃尽那三个互斥名单。
//
// 「未定价的 token」单独算的理由见页面顶上的注释：`cost_usd = 0.0` 是「没有单价」而不
// 是「免费」，加进总额会在几百万 token 上印一个 `$0.0000`，读起来像「这个月没花钱」。
const props = defineProps<{
  /** `/admin/stats/usage` 的响应；`null` = 还没到货。 */
  data: StatsUsage | null
  /** 窗口（天）。 */
  days: StatsDays
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t, locale } = useI18n()

const usage = computed(() => props.data)

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
      loading: props.loading,
      spark: u?.series.map((row) => row.tokens) ?? [],
      ...deltaOf(t, props.days, u?.totals.tokens, u?.prev?.tokens, fmtNum(u?.prev?.tokens ?? 0)),
    },
    {
      key: 'calls',
      label: t('feedback.dashboard.usage.calls'),
      value: num(u?.totals.calls),
      loading: props.loading,
      spark: u?.series.map((row) => row.calls) ?? [],
      ...deltaOf(t, props.days, u?.totals.calls, u?.prev?.calls, fmtNum(u?.prev?.calls ?? 0)),
    },
    {
      key: 'cost',
      label: t('feedback.dashboard.cost.kpi'),
      value: u ? fmtCost(u.totals.cost_usd) : '',
      loading: props.loading,
      spark: u?.series.map((row) => row.cost_usd) ?? [],
      ...deltaOf(t, props.days, u?.totals.cost_usd, u?.prev?.cost_usd, u?.prev ? fmtCost(u.prev.cost_usd) : ''),
      note: costNote,
    },
    {
      key: 'unpriced',
      label: t('feedback.dashboard.cost.unpricedLabel'),
      value: num(u?.totals.unpriced_tokens),
      loading: props.loading,
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
    to: { path: `/projects/${row.project_id}` } as NavTarget,
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

/** 「额度燃尽」标题旁的口径 tip：那句互斥集合的说明。 */
const creditsNote = computed(() => t('feedback.dashboard.credits.note'))

/** 订阅通路身后的 Claude 账号池。**这一块的读数不在数据库里**：计量代理把池子写在
 *  用量账本旁边的一份快照里（后端读它，见 `claude_pool.py`），所以「看不见」是常态
 *  而不是故障 —— 那一行画的是原因，不是空白。读法在 `lib/adminPool.ts`。 */
const pool = computed(() => poolView(usage.value?.claude_accounts, t, locale.value))

const poolNote = computed(() => t('feedback.dashboard.pool.note'))

/** 日期轴的标签：这一屏的 series。 */
const xLabels = computed(() => (usage.value?.series ?? []).map((row) => dayLabel(row.date)))
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
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
      :loading="loading"
    />
    <AdminBarChart :title="t('feedback.dashboard.usage.top')" :rows="topProjects" :loading="loading" />
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
      :loading="loading"
    />
    <AdminBreakTable
      :title="t('feedback.dashboard.usage.byRoute')"
      :note="t('feedback.dashboard.usage.byRouteNote')"
      :rows="byRoute"
      :loading="loading"
    />
  </div>

  <!-- 额度燃尽：三个互斥名单（已耗尽 / 快烧完 / 不限量）。少了它，三个项目同时
             停摆时 token 曲线只是「今天用量下降」，看起来像好消息。 -->
  <section class="ad__split">
    <h2 class="ad__block-title">{{ t('feedback.dashboard.credits.title') }}<AdminNoteTip :text="creditsNote" /></h2>
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
        :loading="loading"
      />
    </div>
    <!-- 不限量数 / 燃烧速率 / 预计用尽：这是**数据行**不是注脚，原位保留。 -->
    <p v-if="credits" class="ad__block-note t-meta-read">
      {{ t('feedback.dashboard.credits.unlimited') }} {{ credits.unlimited_count }} ·
      {{ t('feedback.dashboard.credits.burn') }} {{ credits.burn.credits_per_day.toFixed(1) }}/d ·
      {{ t('feedback.dashboard.credits.eta') }} —
    </p>
  </section>

  <!-- Claude 账号池：订阅通路身后那几张账号，写在计量代理那台机器上。**全部冷却时
       这一块是唯一说得清的话** —— 那会儿 token 曲线只会显示「今天用量下降」，读起来
       像好消息。它也可能是「看不见」（这台部署没跑订阅版代理），那时画的是原因；
       `summary` 为空只在旧后端没这一块时出现，那块整段不画。 -->
  <section v-if="pool.summary" class="ad__pool">
    <h2 class="ad__block-title">{{ t('feedback.dashboard.pool.title') }}<AdminNoteTip :text="poolNote" /></h2>
    <ul v-if="pool.rows.length" class="ad__pool-list">
      <li v-for="row in pool.rows" :key="row.name" class="ad__pool-row">
        <span class="ad__pool-name">{{ row.name }}</span>
        <span class="ad__pool-state">{{ row.state }}</span>
      </li>
    </ul>
    <p class="ad__block-note t-meta-read">{{ pool.summary }}</p>
    <p v-if="pool.stale" class="ad__block-note t-meta-read">{{ pool.stale }}</p>
  </section>
</template>

<style scoped>
/* KPI 网格：N 张卡合成**一条整面板**（一个外框 + 内部分隔线，卡片自己的边框
   与写死高度在 `.ad__kpis` 作用域内关掉，见 AdminKpiCard 的对应块）。边框数量
   从 N 个变 1 个，行高对齐是天生的 —— 不再需要 92/108px 那档妥协。
   面板向左、向下各多伸 1px：第一列格子的左边线与末行格子的下边线被推出外边框、
   由 overflow 裁掉，留下的就全是「缝」。
   窄 2 列 → ≥560 交 `auto-fit`，断点是**容器查询**：量的是面板实际拿到多宽（后台页
   的内容列 `.app-page__column--admin`），不是视口 —— 侧栏折叠省出的宽度视口查询
   看不见。 */

.ad__kpis {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 16px 0 -1px -1px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

/* 列数交给 `auto-fit`，**不写死 4 列**：各类卡数不一样（交付 / 平台 5 张，其余
   4 张），写死 4 列时 5 张卡排成 4 + 1 —— 第二行那一张右边空着三格，面板底色里
   就是一个洞（1440 视口下正好落在这一档）。`auto-fit` 按容器宽度自己定列数，
   4 张卡 4 等分、5 张卡铺满，都不留空轨。 */

@container (min-width: 560px) {
  .ad__kpis {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}

/* **只有两列这一档**（窄屏）才把落单的末位铺满整行：2 列 5 张 = 2 + 2 + 1，最后一行
   半格空白看着像出了错；铺满以后是「2 + 2 + 一整行」，读起来是有意的。
   这里**不能**用「张数是奇数就跨列」这种更宽的判据：它只在列数是偶数时对 —— 5 张卡在
   5 列里本来就铺满一整行（1440 视口下 auto-fit 正好是 5 列），跨列反而把它拆成
   「4 + 1」，那正是这一版要修掉的那个洞。 */

@container (max-width: 559px) {
  .ad__kpis > :deep(*:last-child:nth-child(odd)) {
    grid-column: 1 / -1;
  }
}

/* 格子换成「缝」：gap 0，分隔线用每格自己的上边线 + 左边线，面板负 margin 把
   第一行/列的线推出外边框裁掉（overflow: hidden）。面板内的 hover 底色由卡片
   自己加宽 1px 盖住左侧那条缝（见 AdminKpiCard 的整面板块）。 */

.ad__kpis > :deep(*) {
  border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
}

.ad__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
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

/* 四栏计数。四格并排，和 KPI 行同一套格子，但高度矮一档 —— 它们是同一个总数的四个
   筛选视角，不该和「四个各自独立的数」争同一档视觉重量。 */

.ad__split {
  margin-top: 16px;
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

.ad__block-note {
  margin: 12px 0 0;
}

/* Claude 账号池。两列：账号名在左、状态在右 —— 这一块读的是「哪张账号现在什么状态」，
   竖排成「名字 / 状态 / 名字 / 状态」会让状态那一列对不齐（平台屏那四行机器同一条）。

   状态**不上色**：全页的状态色只留给平台屏的健康四格，这里的状态名（可用 / 冷却中 /
   已停用）自己说得清，再加一层颜色只是多一个要维护的说法。 */

.ad__pool {
  margin-top: 16px;
}

.ad__pool-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ad__pool-row {
  display: contents;
}

.ad__pool-name {
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__pool-state {
  color: var(--ink);
}
</style>
