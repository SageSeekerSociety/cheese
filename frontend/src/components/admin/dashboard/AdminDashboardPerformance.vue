<script setup lang="ts">
import type { StatsPerformance } from '@/lib/adminStats'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'
import { num } from '@/lib/adminStats'
import { fmtMs, fmtNum, fmtSI } from '@/lib/usageFormat'

// 性能那一屏：**这一刻**的接口耗时。
//
// 它是四类里唯一**读进程内存**的（另外三类读库），所以页面上必须把那三件口径说出来：
// 没有窗口（只有此刻）、重启即清零、只覆盖业务 API。不说的话，读者会把它当成「整个
// 平台的、有历史的」数 —— 而它两个都不是。`perf.note` 因此是**常驻**的那条注（每屏至
// 多一条的额度给了它），不进 tip。
//
// 这一屏要回答的问题只有一个：**哪条慢**。所以最慢那条不在表里等人找，直接一条横幅给
// 结论；表按 p95 降序，默认只摆前 8 条（快路由的长尾是噪音），找具体某条交给筛选框。
const props = defineProps<{
  /** `/admin/stats/performance` 的响应；`null` = 还没到货。 */
  data: StatsPerformance | null
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t } = useI18n()

const perf = computed(() => props.data)

/** 投递积压那两格小数字。形状就在 `StatsPerformance` 里（`reliability`），这里不另
 *  外声明一个 —— 声明了就会和接口那一份各自漂移，而漂移的方向恰好是「字段改名了、
 *  页面还在读老名字」。 */

/** 和 p95 同一个道理：**读不到画破折号，不画 0**。`data` 还没到货就是没有。 */
const reliability = computed(() => props.data?.reliability ?? null)

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
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
  <div class="ad__kpis">
    <AdminKpiCard :label="t('feedback.dashboard.perf.active')" :value="num(perf?.active_requests)" :loading="loading" />
    <AdminKpiCard :label="t('feedback.dashboard.perf.uptime')" :value="uptimeText" :loading="loading" />
    <AdminKpiCard :label="t('feedback.dashboard.perf.lag')" :value="lagText" :loading="loading" />
    <AdminKpiCard :label="t('feedback.dashboard.perf.routes')" :value="routesText" :loading="loading" />
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
                class="ad__perf-toggle tap-target"
                :aria-expanded="expandedRoute === `${row.method} ${row.route}`"
                :disabled="sparkDead(row.spark)"
                :aria-label="`${row.method} ${row.route}`"
                @click="toggleRoute(`${row.method} ${row.route}`)"
              >
                <span
                  class="mdi"
                  :class="expandedRoute === `${row.method} ${row.route}` ? 'mdi-chevron-down' : 'mdi-chevron-right'"
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
            <AdminNoteTip :text="t('feedback.dashboard.perf.network.uplinkNote', { iface: netUplink.iface ?? '—' })" />
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
        {{ t('feedback.dashboard.reliability.title') }}<AdminNoteTip :text="t('feedback.dashboard.reliability.note')" />
      </h2>
      <div class="ad__mini-grid">
        <div class="ad__mini">
          <span class="ad__mini-label t-eyebrow-read">{{ t('feedback.dashboard.integrations.delivery.unsent') }}</span>
          <span class="ad__mini-value t-num">{{ num(reliability.delivery_unsent) }}</span>
        </div>
        <div class="ad__mini">
          <span class="ad__mini-label t-eyebrow-read">{{ t('feedback.dashboard.integrations.delivery.dead') }}</span>
          <span class="ad__mini-value t-num">{{ num(reliability.delivery_dead_letters) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
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
  /* 相对定位给 .tap-target：20px 的展开箭头，手指要点得中（§3.6）。 */
  position: relative;
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

/* 格内 label + 口径 tip 同一行（网络吞吐两格）。 */

.ad__cell-head {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ad__cell-note {
  color: var(--muted);
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
