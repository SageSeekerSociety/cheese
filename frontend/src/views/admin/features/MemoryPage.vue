<script setup lang="ts">
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { FeatureDays, MemoryReport } from '@/views/admin/features/featureApi'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminMetricList from '@/components/admin/AdminMetricList.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import { fmtNum } from '@/lib/usageFormat'
import { getMemoryReport } from '@/views/admin/features/featureApi'

// 功能数据的第三页：**记忆**（`/admin/feature-stats/memory`）。文件式记忆在一个项目里
// 长成了什么样——写了多少条、索引撑没撑破注入预算、有没有谁写了却没进索引的文件（或索引
// 指着不存在的文件）、正文是不是太长、整理（dream）最近跑得成不成、正文被读过几次。
//
// 三件读法上的事，决定了版式：
//
// 1. **只数数，不读内容。** 这一页全是计数：项目名可以出现，记忆正文和私人记忆的文件名
//    一个字都不出现。人的记忆按人聚合成「几条、几个人」（见后端模块的文件头）。
// 2. **「没有索引」不是「索引是空的」。** 一个项目没有 `MEMORY.md` 时行数是长破折号，
//    不是 0——0 读起来是「索引是空的」。
// 3. **整理那一档要能看出「挂着没回音」。** 库里的状态还是 `running`，卡没卡住是现在才
//    判得出来的（起点超过一小时还没回音），所以它和完成/失败/被拦并列画。
defineOptions({ name: 'MemoryPage' })

const { t } = useI18n()

const days = ref<FeatureDays>(30)
const report = ref<MemoryReport | null>(null)
const loading = ref(true)
const failed = ref(false)

/** 页签的值走字符串 —— `AdminTabs` 是 `T extends string` 的泛型；回到 `setDays` 里再
 *  收成数字，`days` 是发给接口的参数，别让它变成字符串。 */
const dayOptions = computed(() => [
  { value: '7', label: t('featureStats.days.7') },
  { value: '30', label: t('featureStats.days.30') },
  { value: '90', label: t('featureStats.days.90') },
])

function setDays(value: string) {
  days.value = Number(value) as FeatureDays
}

/** 整数：条数、次数都是整数。空串（没读到）交给卡片画破折号。 */
const count = (value: number | null | undefined) =>
  value === null || value === undefined ? '' : fmtNum(Math.round(value))

/** 一个 ISO 时刻画成「2026-10-04 10:00」。没有值就空串（卡片画破折号）。 */
const stamp = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace('T', ' ') : '')

// 切换窗口后，旧请求晚到也不能覆盖当前窗口。
let requestId = 0
async function load() {
  const current = ++requestId
  loading.value = true
  failed.value = false
  try {
    const next = await getMemoryReport(days.value)
    if (current === requestId) report.value = next
  } catch {
    if (current === requestId) {
      report.value = null
      failed.value = true
    }
  } finally {
    if (current === requestId) loading.value = false
  }
}

// 切窗口**重新取数**（窗口是服务端的问法，`?days=`），不是在这边筛已到的行。
watch(days, load)
onMounted(load)

const numbers = computed(() => report.value?.numbers ?? null)

/** 正文读取折线：一条，全平台每天读了几次正文。 */
const series = computed<ChartSeries[]>(() => [
  {
    name: t('featureStats.memory.trend.reads'),
    values: report.value?.trend.map((point) => point.reads) ?? [],
    style: 'solid',
  },
])

const xLabels = computed(() => report.value?.trend.map((point) => point.date.slice(5)) ?? [])

/* 各张卡的第三行（`delta` 那个道具）。都先判有没有值再拼句子：**没有值的那一行整个
   不画**，不画成「0 / 0」——那读起来是「统计过了，是零」。 */

const personalOf = computed(() => {
  const personal = numbers.value?.personal
  if (!personal || personal.value === 0) return ''
  return t('featureStats.memory.kpi.personalOf', {
    entries: fmtNum(personal.value),
    owners: fmtNum(personal.owners),
  })
})

const indexOf = computed(() => {
  const index = numbers.value?.index
  if (!index || index.over === 0) return ''
  return t('featureStats.memory.kpi.indexOf', {
    lines: fmtNum(index.over_lines),
    bytes: fmtNum(index.over_bytes),
  })
})

const hygieneOf = computed(() => {
  const hygiene = numbers.value?.hygiene
  if (!hygiene) return ''
  const total = hygiene.orphan + hygiene.dangling + hygiene.over_body
  if (total === 0) return ''
  return t('featureStats.memory.kpi.hygieneOf', {
    orphan: fmtNum(hygiene.orphan),
    dangling: fmtNum(hygiene.dangling),
    over: fmtNum(hygiene.over_body),
  })
})

const dreamOf = computed(() => {
  const dream = numbers.value?.dream
  if (!dream) return ''
  return t('featureStats.memory.kpi.dreamOf', {
    refused: fmtNum(dream.refused),
    stuck: fmtNum(dream.stuck),
  })
})

const window_ = computed(() =>
  report.value ? t('featureStats.page.window', { start: report.value.start, end: report.value.end }) : ''
)

/** 待收拾那一格：孤儿 + 悬空 + 超长三样加起来，明细放在 `title` 里。 */
const hygieneRows = computed(() => {
  const hygiene = report.value?.numbers.hygiene
  return [
    { label: t('featureStats.memory.hygiene.orphan'), value: count(hygiene?.orphan) },
    { label: t('featureStats.memory.hygiene.dangling'), value: count(hygiene?.dangling) },
    { label: t('featureStats.memory.hygiene.overBody'), value: count(hygiene?.over_body) },
  ]
})

/** 整理最近十次的结局：完成 / 失败 / 被拦 / 跑着 / 卡住。 */
const dreamRows = computed(() => {
  const dream = report.value?.numbers.dream
  return [
    { label: t('featureStats.memory.dream.completed'), value: count(dream?.completed) },
    { label: t('featureStats.memory.dream.failed'), value: count(dream?.failed) },
    { label: t('featureStats.memory.dream.refused'), value: count(dream?.refused) },
    { label: t('featureStats.memory.dream.stuck'), value: count(dream?.stuck) },
  ]
})

const dreamCaption = computed(() => {
  const last = report.value?.numbers.dream.last_completed_at ?? null
  return t('featureStats.memory.dream.caption', { at: last ? stamp(last) : t('featureStats.memory.never') })
})

/** 每个项目一行。`indexLabel` 是「N 行 / KB」或长破折号（没有索引）；撑破预算时前面
 *  加一个星号。`pending` 是待收拾的总数（孤儿 + 悬空 + 超长）。 */
interface ProjectRow {
  id: string
  name: string
  entries: string
  personal: string
  index: string
  indexWarn: boolean
  pending: string
  pendingTitle: string
  reads: string
  dream: string
  dreamWarn: boolean
}

const projectRows = computed<ProjectRow[]>(() =>
  (report.value?.projects ?? []).map((row) => {
    const hygiene = row.orphan + row.dangling + row.over_body
    const over = row.index.over_lines || row.index.over_bytes
    return {
      id: row.project_id,
      name: row.name || row.project_id.slice(0, 8),
      entries: fmtNum(row.entries),
      personal: row.personal.entries
        ? `${fmtNum(row.personal.entries)}（${fmtNum(row.personal.owners)}）`
        : '',
      index:
        row.index.lines === null
          ? ''
          : `${fmtNum(row.index.lines)} / ${Math.round((row.index.bytes ?? 0) / 1024)}K`,
      indexWarn: over,
      pending: hygiene ? fmtNum(hygiene) : '',
      pendingTitle: t('featureStats.memory.table.pendingTitle', {
        orphan: fmtNum(row.orphan),
        dangling: fmtNum(row.dangling),
        over: fmtNum(row.over_body),
      }),
      reads: row.reads ? fmtNum(row.reads) : '',
      dream: row.dream.stuck
        ? t('featureStats.memory.table.stuck')
        : row.dream.last_completed_at
          ? row.dream.last_completed_at.slice(0, 10)
          : '',
      dreamWarn: row.dream.stuck > 0,
    }
  })
)
</script>

<template>
  <AdminPage
    :title="t('featureStats.features.memory.title')"
    :sub="t('featureStats.features.memory.summary')"
  >
    <template #tools>
      <AdminTabs
        size="sm"
        :label="t('featureStats.page.windowAria')"
        :model-value="String(days)"
        :options="dayOptions"
        @update:model-value="setDays"
      />
    </template>

    <div class="amem__body admin-page__body">
      <p v-if="window_" class="amem__stamp t-meta-read">{{ window_ }}</p>

      <AdminEmptyState
        v-if="failed"
        :title="t('featureStats.page.loadFailed')"
        :action="t('featureStats.page.retry')"
        tone="error"
        @action="load"
      />

      <template v-else>
        <div class="admin-kpi-grid">
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.projects')"
            :value="count(numbers?.projects.value ?? null)"
            :loading="loading"
            :note="t('featureStats.memory.kpi.projectsNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.entries')"
            :value="count(numbers?.entries.value ?? null)"
            :unit="t('featureStats.unit.chars')"
            :loading="loading"
            :note="t('featureStats.memory.kpi.entriesNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.personal')"
            :value="count(numbers?.personal.value ?? null)"
            :unit="t('featureStats.unit.chars')"
            :loading="loading"
            :note="t('featureStats.memory.kpi.personalNote')"
            :delta="personalOf"
            :delta-title="t('featureStats.memory.kpi.personalNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.index')"
            :value="count(numbers?.index.over ?? null)"
            :unit="t('featureStats.memory.kpi.projectsUnit')"
            :loading="loading"
            :note="t('featureStats.memory.kpi.indexNote')"
            :delta="indexOf"
            :delta-title="t('featureStats.memory.kpi.indexNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.hygiene')"
            :value="count((numbers?.hygiene.orphan ?? 0) + (numbers?.hygiene.dangling ?? 0) + (numbers?.hygiene.over_body ?? 0))"
            :loading="loading"
            :note="t('featureStats.memory.kpi.hygieneNote')"
            :delta="hygieneOf"
            :delta-title="t('featureStats.memory.kpi.hygieneNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.reads')"
            :value="count(numbers?.reads.value ?? null)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.memory.kpi.readsNote')"
          />
          <AdminKpiCard
            :label="t('featureStats.memory.kpi.dream')"
            :value="count(numbers?.dream.completed ?? null)"
            :unit="t('featureStats.unit.times')"
            :loading="loading"
            :note="t('featureStats.memory.kpi.dreamNote')"
            :delta="dreamOf"
            :delta-title="t('featureStats.memory.kpi.dreamNote')"
          />
        </div>

        <div class="amem__row">
          <AdminLineChart
            :title="t('featureStats.memory.trend.title')"
            :note="t('featureStats.memory.trend.note')"
            :x-labels="xLabels"
            :series="series"
            :loading="loading"
          />
        </div>

        <div class="amem__row amem__row--equal">
          <AdminMetricList
            :title="t('featureStats.memory.hygiene.title')"
            :note="t('featureStats.memory.hygiene.note')"
            :rows="hygieneRows"
            :loading="loading"
          />
          <AdminMetricList
            :title="t('featureStats.memory.dream.title')"
            :note="t('featureStats.memory.dream.note')"
            :caption="dreamCaption"
            :rows="dreamRows"
            :emphasis="t('featureStats.memory.dream.completed')"
            :loading="loading"
          />
        </div>

        <div class="amem__row">
          <section class="amem__card">
            <h2 class="amem__card-title t-eyebrow-read">
              {{ t('featureStats.memory.table.title') }}
            </h2>
            <p class="amem__card-note t-meta-read">{{ t('featureStats.memory.table.note') }}</p>
            <table class="amem__table">
              <thead>
                <tr>
                  <th scope="col">{{ t('featureStats.memory.table.project') }}</th>
                  <th scope="col" class="amem__num">{{ t('featureStats.memory.table.entries') }}</th>
                  <th scope="col" class="amem__num">{{ t('featureStats.memory.table.personal') }}</th>
                  <th scope="col" class="amem__num">{{ t('featureStats.memory.table.index') }}</th>
                  <th scope="col" class="amem__num">{{ t('featureStats.memory.table.pending') }}</th>
                  <th scope="col" class="amem__num">{{ t('featureStats.memory.table.reads') }}</th>
                  <th scope="col">{{ t('featureStats.memory.table.dream') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="loading && projectRows.length === 0">
                  <td colspan="7" class="amem__empty t-meta-read">{{ t('featureStats.page.loading') }}</td>
                </tr>
                <tr v-else-if="projectRows.length === 0">
                  <td colspan="7" class="amem__empty t-meta-read">{{ t('featureStats.memory.table.empty') }}</td>
                </tr>
                <tr v-for="row in projectRows" :key="row.id">
                  <td class="amem__name">{{ row.name }}</td>
                  <td class="amem__num t-num">{{ row.entries }}</td>
                  <td class="amem__num t-num">{{ row.personal }}</td>
                  <td class="amem__num t-num">
                    <span v-if="row.indexWarn" class="amem__warn" aria-hidden="true">!</span>
                    {{ row.index }}
                  </td>
                  <td class="amem__num t-num" :title="row.pendingTitle">{{ row.pending }}</td>
                  <td class="amem__num t-num">{{ row.reads }}</td>
                  <td :class="{ 'amem__warn-text': row.dreamWarn }">{{ row.dream }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 窗口那行：它是这一页所有数的口径，放在所有数上面一行。常驻不收起。 */
.amem__stamp {
  margin: 0;
  color: var(--muted);
}

.amem__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}

/* 两块并排的起始宽度和看板一样是 720：再窄，两张卡片各自的数字会互相压。 */
@container (min-width: 720px) {
  .amem__row--equal {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}

.amem__card {
  padding: 16px;
}

.amem__card-title {
  margin: 0;
}

.amem__card-note {
  margin: 4px 0 12px;
  color: var(--muted);
}

.amem__table {
  width: 100%;
  border-collapse: collapse;
}

.amem__table th,
.amem__table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  text-align: left;
}

.amem__table th {
  color: var(--muted);
  font-weight: 500;
}

.amem__num {
  text-align: right;
}

.amem__name {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amem__empty {
  color: var(--muted);
}

/* 索引撑破注入预算、或者有整理挂着没回音时的那一个记号。 */
.amem__warn {
  color: var(--warn, #b26a00);
  font-weight: 700;
}

.amem__warn-text {
  color: var(--warn, #b26a00);
}
</style>
