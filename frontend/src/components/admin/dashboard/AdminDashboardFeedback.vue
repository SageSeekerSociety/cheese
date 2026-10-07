<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { KpiRow, PendingRow, StatsDays, StatsFeedback } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import { dayLabel, deltaOf, num, queue } from '@/lib/adminStats'
import { fmtNum } from '@/lib/usageFormat'

// 反馈那一屏：栏位计数 + 两条曲线 + 需处理那十行。
//
// 「需处理」那十行**不在这里取**：它走的是 `loadAdmin` 那条路（不在看板接口里），由
// 取数那一半切好递进来 —— 所以这一屏只画，行里的编号（FB-1042）也是那边算的。
const props = defineProps<{
  /** `/admin/stats/feedback` 的响应；`null` = 还没到货。 */
  data: StatsFeedback | null
  /** 「需处理」那十行（取数那一半从队列里切的）。 */
  pending: PendingRow[]
  /** 那十行的加载态（和看板那一路不是同一个请求）。 */
  listLoading: boolean
  /** 窗口（天）。 */
  days: StatsDays
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const emit = defineEmits<{
  /** 图上点某一天。页面把它拼成队列的地址。 */
  selectDay: [date: string | null]
}>()

const { t } = useI18n()

const feedback = computed(() => props.data)

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
      loading: props.loading,
      to: queue({ assigned: 'none' }),
    },
    {
      key: 'inProgress',
      label: t('feedback.dashboard.kpi.inProgress'),
      value: num(f?.total.open),
      loading: props.loading,
      to: queue({ status: 'in_progress' }),
    },
    {
      key: 'created',
      label: t('feedback.dashboard.kpi.createdInWindow', { d: props.days }),
      value: createdSum === undefined ? '' : fmtNum(createdSum),
      loading: props.loading,
      to: queue({ since: `${props.days}d` }),
      spark: f?.series.map((row) => row.created) ?? [],
      ...deltaOf(t, props.days, createdSum, f?.prev?.created, fmtNum(f?.prev?.created ?? 0)),
    },
    {
      key: 'resolved',
      label: t('feedback.dashboard.kpi.resolvedInWindow', { d: props.days }),
      value: resolvedSum === undefined ? '' : fmtNum(resolvedSum),
      loading: props.loading,
      to: queue({ resolved_since: `${props.days}d` }),
      spark: f?.series.map((row) => row.resolved) ?? [],
      ...deltaOf(t, props.days, resolvedSum, f?.prev?.resolved, fmtNum(f?.prev?.resolved ?? 0)),
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

/** 每种状态一行（梯子四级加「不修复」），全量并排。条的长度按最大的那一行算 —— 它们是
 *  **同一量纲**的划分（加起来等于 `total.all`），所以可以同轴比长短。 */
const feedbackStatusRows = computed(() => {
  const s = feedback.value?.status
  if (!s) return []
  const rows = [
    { key: 'received', label: t('feedback.dashboard.status.received'), value: s.received },
    { key: 'in_progress', label: t('feedback.dashboard.status.inProgress'), value: s.in_progress },
    { key: 'resolved', label: t('feedback.dashboard.status.resolved'), value: s.resolved },
    { key: 'deployed', label: t('feedback.dashboard.status.deployed'), value: s.deployed },
    { key: 'declined', label: t('feedback.dashboard.status.declined'), value: s.declined },
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

/** 日期轴的标签：这一屏的 series。 */
const xLabels = computed(() => (feedback.value?.series ?? []).map((row) => dayLabel(row.date)))

/** 点某一天去队列看那一天收进来的。**日期在这里取**（series 是这一屏的数据），页面
 *  只负责把它拼成地址 —— 只有反馈那一类接这个事件：用量和平台没有对应的筛选参数，
 *  接了就只是「点了没反应」。 */
function onSelectDay(index: number) {
  emit('selectDay', feedback.value?.series[index]?.date ?? null)
}
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
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
      :loading="loading"
      @select="onSelectDay"
    />
    <AdminNumberList
      :title="t('feedback.dashboard.list.title', { count: pending.length })"
      :rows="pending"
      :loading="listLoading"
      :more-to="queue()"
    />
  </div>

  <!-- 状态分布：四级同轴（它们加起来等于总数，所以可以比长短）。 -->
  <AdminBarChart :title="t('feedback.dashboard.status.title')" :rows="feedbackStatusRows" :loading="loading" />
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
</style>
