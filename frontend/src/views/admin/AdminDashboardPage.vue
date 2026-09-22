<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import type { StatsKind } from '@/api'
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'

import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminNumberList from '@/components/admin/AdminNumberList.vue'
import { fmtCost, fmtNum } from '@/lib/usageFormat'
import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的看板（§4.2）。**它读的是整个平台，不只是反馈。**
//
// 分类是这一页的骨架（需求方原话：「看板我觉得不是专门为反馈打造的？其它的也应该算
// 进去，比如 token 消费之类的，然后表也不要太多……不然人的注意力比较稀疏」）。三类
// 各占一屏，切到哪一类才拉哪一类 —— 服务端就是三条接口（`/admin/stats/{feedback,
// usage,platform}`），而这三块里有两块读的是全平台增长最快的表（`resource_usage` 每
// 调一次 `/v1/messages` 长一行），没有理由让「看一眼反馈」把用量也读一遍。
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
const KINDS: StatsKind[] = ['feedback', 'usage', 'platform']

/** 每个分类的名字。**写成一张键名字面量的表**，不在模板里拼
 *  `feedback.dashboard.tab.${kind}` —— 拼出来的键在源码里没有一处字面量出现，
 *  `catalog.spec.ts` 的「这个键没有任何文件引用」那条闸门就会把这三个键判成没人用的
 *  死词条（它扫的是源码文本，不是运行时的调用）。拼字符串在这里省下的是一行，代价是
 *  每次跑门禁都要重新解释一遍「这三个键其实是活的」。 */
const TAB_KEY: Record<StatsKind, string> = {
  feedback: 'feedback.dashboard.tab.feedback',
  usage: 'feedback.dashboard.tab.usage',
  platform: 'feedback.dashboard.tab.platform',
}

/** 队列的地址。写**地址**不写路由名：规格 §11 第 9 条钉的是地址。 */
const QUEUE = '/admin/queue'
const queue = (query: Record<string, string> = {}): RouteLocationRaw => ({ path: QUEUE, query })

/** 迷你列表最多画几行。和 `AdminNumberList` 的 `SHOWN` 是同一个数。 */
const SHOWN = 10

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

const counts = computed(() => store.counts)

/** 反馈的 KPI。前两张走**列表接口**的计数（`loadAdmin` 带回来的 `counts`），后两张走
 *  看板接口 —— 两个请求并发，互不依赖。 */
const feedbackKpis = computed(() => [
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

/** 反馈的两条线。`created` 实线、`resolved` 虚线（§7.5：颜色由组件按线型发，这一层
 *  只管名字和值）。 */
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
    loading: store.statsLoading && store.statsKind === 'usage',
    to: queue(),
  },
  {
    key: 'calls',
    label: t('feedback.dashboard.usage.calls'),
    value: num(usage.value?.totals.calls),
    loading: store.statsLoading && store.statsKind === 'usage',
    to: queue(),
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

/** 用量最高的几个项目。柱子只报 token —— 同一根柱子上再叠一个「花了多少钱」，两根
 *  柱子的长度就各自代表不同的东西，而长短本来是用来比的。 */
const topProjects = computed(() =>
  (usage.value?.top_projects ?? []).map((row) => ({ label: row.name, value: row.tokens }))
)

const costText = computed(() => {
  const totals = usage.value?.totals
  return totals === undefined
    ? ''
    : t('feedback.dashboard.cost.value', { calls: fmtNum(totals.calls), usd: fmtCost(totals.cost_usd) })
})

/* ---- 平台那一块 ---- */

const peopleKpis = computed(() => [
  {
    key: 'accounts',
    label: t('feedback.dashboard.people.total'),
    value: num(platform.value?.people.total),
    loading: store.statsLoading && store.statsKind === 'platform',
  },
  {
    key: 'new',
    label: t('feedback.dashboard.people.new'),
    value: num(platform.value?.people.new),
    loading: store.statsLoading && store.statsKind === 'platform',
  },
  {
    key: 'admins',
    label: t('feedback.dashboard.people.admins'),
    value: num(platform.value?.people.admins),
    loading: store.statsLoading && store.statsKind === 'platform',
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

      <!-- 分类控件是这一页的**第一个控件**：读的人先决定看哪一类，再看数字。
           `v-btn-toggle` 而不是 tabs —— tabs 底下那条线会跟页头那条 `--line-2` 抢同一种
           「这里是边界」的意思，而分类不是边界，是一次筛选。 -->
      <div class="ad__kinds">
        <v-btn-toggle
          :model-value="store.statsKind"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
          @update:model-value="selectKind($event as StatsKind)"
        >
          <v-btn v-for="k in KINDS" :key="k" :value="k" size="small">
            {{ t(TAB_KEY[k]) }}
          </v-btn>
        </v-btn-toggle>
      </div>

      <!-- 错误是**整块**的（§9.3）：这一页的主文案只有这一句，页头留着 —— 它是这一页
           的名字，不是数据。 -->
      <p v-if="failed" class="ad__none">
        <span class="ad__none-title">{{ t('feedback.dashboard.error.title') }}</span>
        <span class="ad__none-desc">{{ t('feedback.dashboard.error.desc') }}</span>
      </p>

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

        <div class="ad__cost" :class="{ 'ad__cost--partial': !store.statsLoading && !usage }">
          <span class="ad__cost-label t-meta-read">{{ t('feedback.dashboard.cost.label') }}</span>
          <v-skeleton-loader v-if="store.statsLoading" type="text" class="ad__cost-skel" />
          <span v-else-if="usage" class="ad__cost-value t-meta-read t-num">{{ costText }}</span>
          <!-- §9.3 第 4 态：这一趟没拿到就说没拿到，别拿 0 冒充读数。 -->
          <template v-else>
            <span class="ad__cost-value t-meta-read">{{ t('feedback.dashboard.partial.title') }}</span>
            <span class="ad__cost-hint t-meta-read" :title="t('feedback.dashboard.partial.hint')">?</span>
          </template>
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

.ad__cost {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.ad__cost--partial {
  color: var(--muted);
}

.ad__cost-label {
  flex: 0 0 auto;
}

.ad__cost-value {
  flex: 1;
  min-width: 0;
}

.ad__cost-hint {
  cursor: help;
  color: var(--muted);
}

.ad__cost-skel {
  flex: 1;
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
