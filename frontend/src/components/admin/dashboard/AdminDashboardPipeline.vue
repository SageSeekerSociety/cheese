<script setup lang="ts">
import type { KpiRow, StatsPipeline } from '@/lib/adminStats'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminActionList from '@/components/admin/AdminActionList.vue'
import AdminBreakTable from '@/components/admin/AdminBreakTable.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLiveSpine from '@/components/admin/AdminLiveSpine.vue'
import { num } from '@/lib/adminStats'

// 交付管线那一屏：五张卡 + 活四站导轨 + 「等你处理 / 卡住了」+ 失败码与机器连败。
//
// 五张卡、四站、每一张表的行都从 `data`（`/admin/stats/pipeline` 的响应）现算：哪几站
// 算通过量、哪些卡点算「需要人」是**这一屏的读法**，摆在别处就会和画法分家。
const props = defineProps<{
  /** `/admin/stats/pipeline` 的响应；`null` = 还没到货。 */
  data: StatsPipeline | null
  /** 这一类的加载态（骨架）。 */
  loading: boolean
}>()

const { t } = useI18n()

const pipeline = computed(() => props.data)

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
      loading: props.loading,
      note: t('feedback.dashboard.pipeline.backlog.note'),
    },
    {
      key: 'stuck',
      label: t('feedback.dashboard.pipeline.kpi.stuck'),
      value: num(p?.backlog.stuck),
      loading: props.loading,
    },
    {
      key: 'dwell',
      label: t('feedback.dashboard.pipeline.kpi.dwell'),
      value: hoursText(p?.dwell.filed_to_decision.p50_seconds ?? null),
      loading: props.loading,
    },
    {
      key: 'needs',
      label: t('feedback.dashboard.pipeline.kpi.needsYou'),
      value: num(
        (p?.needs_you.reviewer_pending ?? 0) + (p?.needs_you.open_tasks ?? 0) + (p?.needs_you.awaiting_answer ?? 0)
      ),
      loading: props.loading,
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
      loading: props.loading,
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
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
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
  <AdminLiveSpine :stages="spineStages" :stuck="spineStuck" :loading="loading" />

  <div class="ad__row">
    <AdminActionList
      :title="t('feedback.dashboard.pipeline.needs.title')"
      :rows="needsYouRows"
      :loading="loading"
      :empty="t('feedback.dashboard.pipeline.needs.empty')"
      :note="t('feedback.dashboard.pipeline.needs.note')"
    />
    <AdminActionList
      :title="t('feedback.dashboard.pipeline.stuck.title')"
      :rows="stuckRows"
      :loading="loading"
      :empty="t('feedback.dashboard.pipeline.stuck.empty')"
      :note="t('feedback.dashboard.pipeline.stuck.note')"
    />
  </div>

  <div class="ad__row ad__row--equal">
    <AdminBreakTable
      :title="t('feedback.dashboard.pipeline.failures.title')"
      :note="t('feedback.dashboard.pipeline.failures.note')"
      :rows="failureRows"
      :loading="loading"
    />
    <AdminActionList
      :title="t('feedback.dashboard.pipeline.host.title')"
      :rows="hostRows"
      :loading="loading"
      :empty="t('feedback.dashboard.pipeline.host.empty')"
      :note="t('feedback.dashboard.pipeline.host.note')"
    />
  </div>
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
</style>
