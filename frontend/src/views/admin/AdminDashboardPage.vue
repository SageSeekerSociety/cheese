<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import { fmtCost, fmtNum } from '@/lib/usageFormat'
import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的看板（§4.2）。列宽 1100，两条算式各钉一排东西：
// `4 × 263 + 3 × 16 = 1100`（KPI 行）与 `660 + 24 + 416 = 1100`（图 + 迷你列表）。
//
// **这一页的琥珀出现 0 次**（§3 F-01）：图用中性灰阶 + 线型区分系列（§7.5），KPI 大
// 数字走 `--ink`，页头下沿是 `--line-2`（那条线全站只出现两处：表头下、页头下）。
//
// **墙上每个数字都写得出点下去去哪**（§6.3 那张准入表的规则：把这一页所有数字的点击
// 禁掉之后，这里应该变得没有价值）。所以：拿不到来源的数不上墙（成员总数 / 按天新增
// 成员 —— 前端这条接口里没有平台账号的计数，见下），上了墙的每一个都有 `to`。
//
// 两个请求，两块数据：管理列表接口（`loadAdmin`）给 `counts` 和第一页卡片，看板接口
// （`loadStats`）给窗口内的曲线和成本。**没有第三个** —— 计数那两个键
// （`unassigned` / `deployed`）只在管理端的列表响应里（`FeedbackService.counts`
// 的 docstring 写着「rides on /admin/feedback and nowhere else」），所以拉这两个数的
// 那一趟顺手把迷你列表的十行也带回来了。
defineOptions({ name: 'AdminDashboardPage' })

const store = useFeedbackStore()
const { t } = useI18n()
const router = useRouter()

/** 页头上那句「过去 7 天」问的窗口。窗口是**页面**的问题（`loadStats` 收参数）。 */
const DAYS = 7

/** 迷你列表最多画几行。和 `AdminNumberList` 的 `SHOWN` 是同一个数，这里再写一份是
 *  因为标题上那个数得跟**下面真的画出来的行数**一致：区块标题说「需处理 · 10」、
 *  下面摆着三行，比不写还难读。 */
const SHOWN = 10

/** 队列的地址。写**地址**不写路由名：`/admin/queue` 是后面才注册的路由，名字还没定，
 *  而规格 §11 第 9 条钉的是地址。 */
const QUEUE = '/admin/queue'

const queue = (query: Record<string, string> = {}): RouteLocationRaw => ({ path: QUEUE, query })

/** 数字串。拿不到来源（`undefined`）时给空串，卡片自己画成 `—`；给 0 的话「没读到」
 *  和「读出来确实是零」在屏幕上就分不开了（`AdminKpiCard` 文件开头写的就是这条）。
 *
 *  `store.error` 非空时也一律留空：它是**全 store 共用**的一个字段，两趟请求并发时
 *  后到的那个会把先到的那句错话擦掉，所以它只答得了一件事 —— 「这一屏刚出过事」。
 *  答得了的那一件用上就够：出过事的时候不拿 `counts` 初值里的那几个 0 冒充读数。 */
const num = (v: number | undefined): string => (v === undefined || store.error !== null ? '' : fmtNum(v))

const counts = computed(() => store.counts)

/** 曲线上的和。`stats` 还没到（或这一趟没成）时给空串，不给 0，理由同 `num`。 */
const sumOf = (pick: 'created' | 'resolved'): string =>
  store.stats ? fmtNum(store.stats.series.reduce((acc, row) => acc + row[pick], 0)) : ''

/** 「7 日上线」这一格**没有来源**：`AdminFeedbackStats.series` 这一版只有 `created` /
 *  `resolved` 两条曲线（后端的 `/admin/stats` 里其实回 `deployed`，前端这条接口的形状
 *  还没跟上），所以这个窗口数拿不到。卡片照画、数字位留空画成 `—`，并且**不拿累计的
 *  「已上线」顶替** —— 「这七天上线了几条」和「一共上线了几条」是两个问题，混着用会
 *  让人在下一周看不出变化。它是 §9.3 第 4 态那套读法的同一件事：拿不到就说拿不到，
 *  卡片不许因此消失（消失会让 KPI 行少一格，`4 × 263 + 3 × 16` 当场失效）。 */
const DEPLOYED_7D = ''

/** 墙上那一摞数字，顺序就是屏幕上从左到右、从上到下的顺序：第一排四张和 §4.2 画的
 *  那四张逐字相同（待分诊 / 进行中 / 7 日解决 / 7 日上线），其余按 §6.3 的表往下排。
 *  §4.2 那张图只画了一排，而准入表里有 14 个数要上墙 —— 一张速写画不下，格子照 263
 *  一格一格往下排。 */
interface Kpi {
  key: string
  label: string
  value: string
  /** 只有「7 日上线」用得上（它得说明自己为什么是空的）。 */
  unit?: string
  loading: boolean
  /** 每一张都有：准入表上留着而点不动的数字不该上墙。 */
  to: RouteLocationRaw
}

const kpis = computed<Kpi[]>(() => [
  {
    key: 'untriaged',
    label: t('feedback.dashboard.kpi.untriaged'),
    value: num(counts.value.unassigned),
    loading: store.adminLoading,
    to: queue({ assigned: 'none' }),
  },
  {
    key: 'inProgress',
    label: t('feedback.dashboard.kpi.inProgress'),
    value: num(counts.value.active),
    loading: store.adminLoading,
    to: queue({ status: 'in_progress' }),
  },
  {
    key: 'resolved7d',
    label: t('feedback.dashboard.kpi.resolved7d'),
    value: sumOf('resolved'),
    loading: store.statsLoading,
    to: queue({ resolved_since: '7d' }),
  },
  {
    key: 'deployed7d',
    label: t('feedback.dashboard.kpi.deployed7d'),
    value: DEPLOYED_7D,
    unit: t('feedback.dashboard.kpi.noSeries'),
    loading: store.statsLoading,
    to: queue({ deployed_since: '7d' }),
  },
  {
    key: 'created7d',
    label: t('feedback.dashboard.kpi.created7d'),
    value: sumOf('created'),
    loading: store.statsLoading,
    to: queue({ since: '7d' }),
  },
  {
    key: 'resolvedTotal',
    label: t('feedback.dashboard.kpi.resolvedTotal'),
    value: num(counts.value.resolved),
    loading: store.adminLoading,
    to: queue({ status: 'resolved' }),
  },
  {
    key: 'deployedTotal',
    label: t('feedback.dashboard.kpi.deployedTotal'),
    value: num(counts.value.deployed),
    loading: store.adminLoading,
    to: queue({ status: 'deployed' }),
  },
  {
    key: 'all',
    label: t('feedback.dashboard.kpi.all'),
    value: num(counts.value.all),
    loading: store.adminLoading,
    // 队列（无参）就是「全部」那一栏，带上任何参数都成了另一个问题。
    to: queue(),
  },
  {
    key: 'hot',
    label: t('feedback.dashboard.kpi.hot'),
    value: num(counts.value.hot),
    loading: store.adminLoading,
    to: queue({ hot: '1' }),
  },
  {
    key: 'unread',
    label: t('feedback.dashboard.kpi.unread'),
    value: num(counts.value.unread),
    loading: store.adminLoading,
    to: queue({ unread: '1' }),
  },
])

/** 图上的两条线。`created` 实线、`resolved` 虚线（§7.5：颜色由组件按线型发，这一层
 *  只管名字和值）。 */
const series = computed<ChartSeries[]>(() => [
  {
    name: t('feedback.dashboard.chart.created'),
    values: store.stats?.series.map((row) => row.created) ?? [],
    style: 'solid',
  },
  {
    name: t('feedback.dashboard.chart.resolved'),
    values: store.stats?.series.map((row) => row.resolved) ?? [],
    style: 'dashed',
  },
])

/** 轴标签写「9/15」：轴上七个点，写全年月日是七串数字挤在一起，而窗口在页头上已经
 *  说了是过去七天。 */
const xLabels = computed(() => store.stats?.series.map((row) => dayLabel(row.date)) ?? [])

function dayLabel(date: string): string {
  // 后端给的是 `YYYY-MM-DD`（或带时间的 ISO 串），取前两段就够，别交给 `Date`
  // 去解析 —— 那会按本地时区把日期挪一天。
  const [, month, day] = date.slice(0, 10).split('-')
  return `${Number(month)}/${Number(day)}`
}

/** 点某一天去队列看那一天收进来的。用 ISO 的 `YYYY-MM-DD` 而不是轴上那个「9/15」：
 *  筛选参数是给机器读的，月份写在前面就不会有「哪个是月」的问题。 */
function onSelectDay(index: number): void {
  const date = store.stats?.series[index]?.date
  if (!date) return
  void router.push(queue({ since: date.slice(0, 10) }))
}

/** 迷你列表的行。**「需处理」只有两种状态**（还没人接手、有人在做），所以在这一页
 *  已经拿到的那摞卡片里筛这两档 —— 筛的是状态，不是再发一次请求：那十行本来就跟着
 *  `loadAdmin()` 一起回来了。
 *
 *  更新时间取 `last_activity_at`，为空退回 `created_at`（后端的 `last_activity` 是
 *  一条 timeline 查询，还没人动过的条目回 null；那一格的语义是「最近一次动静」，一条
 *  都没动过的条目上，提交就是它唯一的动静）。 */
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

/** 「FB-1042」里那串数字。列表左列要的是一个能纵向对齐、能排序的编号，`display_id`
 *  里那一段就是它。 */
function displayNo(displayId: string): number {
  const digits = displayId.replace(/\D/g, '')
  return digits === '' ? 0 : Number(digits)
}

/** 成本那一行。`cost` 为 null 就是 §9.3 的第 4 态（用量那本账不在反馈域里，拿不到），
 *  只降级这一行，其余数字照画。 */
const cost = computed(() => store.stats?.cost ?? null)
const costText = computed(() => {
  const c = cost.value
  return c === null ? '' : t('feedback.dashboard.cost.value', { calls: fmtNum(c.calls), usd: fmtCost(c.usd) })
})

/** 整页失败 = 看板接口这一趟什么都没拿到。数字那一块来自另一个接口，它单独失败时
 *  这一页照样画（数字位留空，见 `num`）—— 「一个接口挂了」不该把另一块也带走。 */
const failed = computed(() => !store.statsLoading && store.stats === null && store.error !== null)

/** 空态：公开那一路一条都没有，而且没有任何一条还没人接手。`unassigned` 是唯一把
 *  private / security 也算进去的那个数，所以这两个都归零才是「平台还没有产生反馈」。
 *
 *  `stats !== null` 这一条不是多余的：`counts` 的初值就是全 0，光看它的话，挂载前
 *  那一帧（骨架还没开始转）会被读成「一条反馈都没有」。 */
const empty = computed(
  () =>
    !store.statsLoading &&
    !store.adminLoading &&
    store.stats !== null &&
    (store.counts.all ?? 0) === 0 &&
    (store.counts.unassigned ?? 0) === 0
)

onMounted(() => {
  // 两个请求并发发出去：计数和曲线互不依赖，串行只让首屏多等一个来回。
  void store.loadAdmin()
  void store.loadStats(DAYS)
})
</script>

<template>
  <div class="ad">
    <div class="ad__inner page-container--wide">
      <header class="ad__head">
        <h1 class="t-console-title">{{ t('feedback.dashboard.title') }}</h1>
        <span class="ad__window t-meta-read">{{ t('feedback.dashboard.window') }}</span>
      </header>

      <!-- 错误与空是**整块**的（§9.3）：这一页的主文案只有这两句，页头留着 —— 它是
           这一页的名字，不是数据。 -->
      <p v-if="failed" class="ad__none">
        <span class="ad__none-title">{{ t('feedback.dashboard.error.title') }}</span>
        <span class="ad__none-desc">{{ t('feedback.dashboard.error.desc') }}</span>
      </p>

      <p v-else-if="empty" class="ad__none">
        <span class="ad__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
        <span class="ad__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
      </p>

      <template v-else>
        <div class="ad__kpis">
          <AdminKpiCard
            v-for="kpi in kpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :unit="kpi.unit"
            :loading="kpi.loading"
            :to="kpi.to"
          />
        </div>

        <div class="ad__row">
          <AdminLineChart
            :title="t('feedback.dashboard.chart.title')"
            :x-labels="xLabels"
            :series="series"
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

        <div class="ad__cost" :class="{ 'ad__cost--partial': !store.statsLoading && cost === null }">
          <span class="ad__cost-label t-meta-read">{{ t('feedback.dashboard.cost.label') }}</span>
          <v-skeleton-loader v-if="store.statsLoading" type="text" class="ad__cost-skel" />
          <template v-else-if="cost">
            <span class="ad__cost-value t-meta-read t-num">{{ costText }}</span>
          </template>
          <!-- §9.3 第 4 态：只降这一行，别的数字不受影响。`?` 上的 `title` 是给鼠标
               用户的原文，可见的两句是给所有人的 —— 一个只有 `title` 的说明等于没有
               说明（触屏和键盘都碰不到它）。 -->
          <template v-else>
            <span class="ad__cost-value t-meta-read">{{ t('feedback.dashboard.partial.title') }}</span>
            <span class="ad__cost-value t-meta-read">{{ t('feedback.dashboard.partial.desc') }}</span>
            <span class="ad__cost-hint t-meta-read" :title="t('feedback.dashboard.partial.hint')">?</span>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 滚动归这一页自己领：外壳（`AdminLayout` 的 `.admin-shell__main`）只让高度和宽度，
   不给滚动（和 `AdminMembersPage` 同一套约定）。KPI 那三排加起来比一屏高，少了
   `overflow-y: auto` 下半截会被外面那层 `overflow-hidden` 裁掉，而且没人能滚。 */
.ad {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 16px 24px 24px;
  overflow-y: auto;
}

/* 1100 那一列的水平居中。宽度走 `page-container--wide`（全站两个列宽之一），这里
   只管位置。 */
.ad__inner {
  display: flex;
  flex-direction: column;
  margin: 0 auto;
}

/* 页头 56px，下沿用 `--line-2`（§4.2）。这条线是页头与内容之间那道**比行分隔线重**
   的界，而全站只有两处用它。 */
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

/* KPI 一排四张、263 一格（§4.2 的算式）。卡片自己写死了宽高，所以这里用固定列宽而
   不是 `1fr` —— 让列去分配宽度的话，四张卡会按内容各取一个数，那一行就不再是 1100。 */
.ad__kpis {
  display: grid;
  grid-template-columns: repeat(4, 263px);
  gap: 16px;
  padding-top: 16px;
}

/* 图 660 + 缝 24 + 迷你列表 416 = 1100（§4.2 的第二条算式）。两块**按顶对齐**：
   图的高度由画布和那张折叠表决定，列表的高度由行数决定，谁都不该被拉到对方那么高。 */
.ad__row {
  display: grid;
  grid-template-columns: 660px 416px;
  gap: 24px;
  align-items: start;
  padding-top: 16px;
}

/* 成本那行 18px（§4.2）。它是这一页唯一**不是**链接的数字（§6.3 第 17 项：只显示合计、
   不拆天，而且不上墙为可点数字），所以它不是卡片、没有 `to`、也不跟 KPI 那一摞挤。 */
.ad__cost {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-height: var(--lh-12);
  margin-top: 16px;
  border-top: 1px solid var(--line);
}

/* 第 4 态（§9.3）只作用在这一行上：底色降一档，其余数字照画。 */
.ad__cost--partial {
  padding: 0 8px;
  background: var(--fill);
}

.ad__cost-label {
  flex: 0 0 auto;
}

.ad__cost-value {
  flex: 0 0 auto;
}

/* 那个 `?`：告诉人「这里少了一个数」这件事本身有一个理由可查（`title`）。 */
.ad__cost-hint {
  flex: 0 0 auto;
  cursor: help;
}

.ad__cost-skel {
  width: 200px;
}

.ad__cost-skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

/* 空态 / 错误态的内容块和两张卡里的那一套同形（主 15/600/`--ink`，副 13/`--muted`，
   中间 8px）：同一页面上「这里没东西」的两种说法不该长得不一样。 */
.ad__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding-top: 32px;
}

.ad__none-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.ad__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
