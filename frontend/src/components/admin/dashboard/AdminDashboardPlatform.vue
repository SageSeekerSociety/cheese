<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { KpiRow, StatsDays, StatsPlatform } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminShareBar from '@/components/admin/AdminShareBar.vue'
import { dayLabel, deltaOf, num } from '@/lib/adminStats'
import { fmtNum } from '@/lib/usageFormat'

// 平台那一屏：账号的存量与新增、机器台账的存量、健康度，以及配额与缺口那两张分布。
//
// 机器那四行是**存量，不是在线数** —— 在线状态住在进程内存里，库里没有可以查的那一列
// （`platform_stats/repositories.py` 的模块 docstring）。这句话必须写在页面上：一个
// 「机器 5」的数字，读的人默认会当成「现在有 5 台在跑」。它是这一屏**常驻**的那条注。
const props = defineProps<{
  /** `/admin/stats/platform` 的响应；`null` = 还没到货。 */
  data: StatsPlatform | null
  /** 窗口（天）。 */
  days: StatsDays
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t } = useI18n()

const platform = computed(() => props.data)

const peopleKpis = computed<KpiRow[]>(() => [
  {
    key: 'accounts',
    label: t('feedback.dashboard.people.total'),
    value: num(platform.value?.people.total),
    loading: props.loading,
  },
  // 真人 / agent 分开报，不是一个总数让人自己猜。判据是 `agent_bindings`（和后端
  // `IdentityService.is_agent` 同一份），所以这两个数必然加得回 `total`。
  {
    key: 'humans',
    label: t('feedback.dashboard.people.humans'),
    value: num(platform.value?.people.humans),
    loading: props.loading,
  },
  {
    key: 'agents',
    label: t('feedback.dashboard.people.agents'),
    value: num(platform.value?.people.agents),
    loading: props.loading,
  },
  {
    key: 'new',
    label: t('feedback.dashboard.people.newInWindow', { d: props.days }),
    value: num(platform.value?.people.new),
    loading: props.loading,
    spark: platform.value?.people.series.map((row) => row.created) ?? [],
    ...deltaOf(
      t,
      props.days,
      platform.value?.people.new,
      platform.value?.people.prev_new,
      fmtNum(platform.value?.people.prev_new ?? 0)
    ),
  },
  {
    key: 'admins',
    label: t('feedback.dashboard.people.admins'),
    value: num(platform.value?.people.admins),
    loading: props.loading,
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
    key: 'hosts',
    label: t('feedback.dashboard.machines.hosts'),
    value: num(platform.value?.machines.cloud_hosts),
  },
])

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
  if (x.machines.host_slots_total > 0) {
    rows.push({
      label: t('feedback.dashboard.extras.hostSlots.title'),
      valueText: `${num(x.machines.host_slots_used)} / ${num(x.machines.host_slots_total)}`,
      limit: x.machines.host_slots_total,
      ratio: x.machines.host_slots_used / x.machines.host_slots_total,
      tone: 'ink',
      hint: '',
      note: t('feedback.dashboard.extras.hostSlots.note'),
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

/** 常驻机器/云端宿主机的状态名 → 词条键。**字面量键表 + 原名兜底**（同 `HEALTH_KEY` 的
 *  模式）：拼出来的键 `catalog.spec.ts` 会判死键；台账里冒出表里没有的新状态时，
 *  原样显示状态名，不静默吞掉。 */
const MACHINE_STATE_KEY: Record<string, string> = {
  preparing: 'feedback.dashboard.extras.machineState.preparing',
  ready: 'feedback.dashboard.extras.machineState.ready',
  claimed: 'feedback.dashboard.extras.machineState.claimed',
  error: 'feedback.dashboard.extras.machineState.error',
}

const HOST_STATUS_KEY: Record<string, string> = {
  provisioning: 'feedback.dashboard.extras.hostStatus.provisioning',
  starting: 'feedback.dashboard.extras.hostStatus.starting',
  running: 'feedback.dashboard.extras.hostStatus.running',
  deleting: 'feedback.dashboard.extras.hostStatus.deleting',
  deleted: 'feedback.dashboard.extras.hostStatus.deleted',
  error: 'feedback.dashboard.extras.hostStatus.error',
  unknown: 'feedback.dashboard.extras.hostStatus.unknown',
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
const hostSegments = computed(() => stateSegments(extras.value?.machines.host_by_status, HOST_STATUS_KEY))

/** 平台的健康度。四格并排，**状态色只在这里用**（up / stalling / down）—— 全页别处
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
  routes: 'feedback.dashboard.health.routes',
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

/** 日期轴的标签：这一屏的 series（新增账号的逐日）。 */
const xLabels = computed(() => (platform.value?.people.series ?? []).map((row) => dayLabel(row.date)))
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
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
      :loading="loading"
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
        :loading="loading"
      />
      <AdminShareBar
        v-if="warmSegments.length"
        :title="t('feedback.dashboard.extras.warmStates')"
        :segments="warmSegments"
        :note="t('feedback.dashboard.extras.machines.note')"
        :loading="loading"
      />
      <AdminShareBar
        v-if="hostSegments.length"
        :title="t('feedback.dashboard.extras.hostStates')"
        :segments="hostSegments"
        :note="t('feedback.dashboard.extras.machines.note')"
        :loading="loading"
      />
    </div>
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

/* 健康度。**状态色只在这一块用**（up / stalling / down），全页别处都是中性阶：一屏里
   只有一处有颜色的时候，那一处就是「需要看的地方」。 */

.ad__health {
  margin-top: 16px;
}

.ad__health-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}

/* 列数跟着检查项数走，和上面 `.ad__kpis` 同一个理由：写死 3 列时第四项落单一行。 */
@container (min-width: 720px) {
  .ad__health-grid {
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
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
