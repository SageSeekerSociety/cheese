<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { KpiRow, StatsDays, StatsProduct } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import { dayLabel, deltaOf, num } from '@/lib/adminStats'
import { fmtNum } from '@/lib/usageFormat'

// 产品健康那一屏：北极星 + 两条护栏 + 两条「今天算不出来」。
//
// 两条算不出来的（`unavailable`）**带理由**画，不画一个假 0 —— 名字是 snake_case，
// 词条键在这张表里写全（理由同 `TAB_KEY`：拼出来的键在源码里没有一处字面量出现）。
const props = defineProps<{
  /** `/admin/stats/product` 的响应；`null` = 还没到货。 */
  data: StatsProduct | null
  /** 窗口（天）。页头那个切换器改的就是它。 */
  days: StatsDays
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t } = useI18n()

const product = computed(() => props.data)

const productKpis = computed<KpiRow[]>(() => [
  {
    key: 'north',
    label: t('feedback.dashboard.product.northStar', { d: props.days }),
    value: num(product.value?.north_star.total),
    loading: props.loading,
    spark: product.value?.north_star.series.map((row) => row.accepted) ?? [],
    ...deltaOf(
      t,
      props.days,
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
    loading: props.loading,
  },
  {
    key: 'useful',
    label: t('feedback.dashboard.product.usefulness.title'),
    value:
      product.value?.usefulness.useful_rate === null || product.value?.usefulness.useful_rate === undefined
        ? ''
        : `${Math.round(product.value.usefulness.useful_rate * 100)}%`,
    loading: props.loading,
  },
  {
    key: 'dismiss',
    label: t('feedback.dashboard.product.usefulness.down'),
    value: num(product.value?.usefulness.proposal_dismissals),
    loading: props.loading,
  },
])

const northSeries = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.product.northStar', { d: props.days }),
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

const productUnavailable = computed(
  () =>
    product.value?.unavailable.map((row) => ({
      name: row.name,
      text: t(PRODUCT_UNAVAILABLE_KEY[row.name] ?? row.name),
    })) ?? []
)

const PRODUCT_UNAVAILABLE_KEY: Record<string, string> = {
  acceptance_rate_after_summon: 'feedback.dashboard.product.unavailable.summon',
  churn_after_credits_exhausted: 'feedback.dashboard.product.unavailable.churn',
}

/** 日期轴的标签：这一屏的 series（北极星的逐日）。**按类显式取自己那一份**：产品类
 *  曾经落到 feedback 的 series 上 —— 今天恰好同窗口所以没错，但那是隐性依赖。 */
const xLabels = computed(() => (product.value?.north_star.series ?? []).map((row) => dayLabel(row.date)))
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
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
      :title="t('feedback.dashboard.product.northStar', { d: days })"
      :x-labels="xLabels"
      :series="northSeries"
      :loading="loading"
      :note="t('feedback.dashboard.product.northStarNote')"
    />
    <AdminShareBar
      :title="t('feedback.dashboard.product.rejection.title')"
      :segments="rejectionSegments"
      :note="t('feedback.dashboard.product.rejection.note')"
      :loading="loading"
    />
  </div>

  <AdminShareBar
    :title="t('feedback.dashboard.product.usefulness.title')"
    :segments="usefulnessSegments"
    :note="t('feedback.dashboard.product.usefulness.note')"
    :loading="loading"
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

.ad__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
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
