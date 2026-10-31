<script setup lang="ts">
/**
 * Render CI and queue metrics supplied by the container page. Empty samples
 * remain gaps, each headline states its measurement window, and incomplete
 * backfill stays visible so partial history cannot read as a complete period.
 */
import type { ChartSeries } from '@/components/admin/AdminLineChart.vue'
import type { CiBoard, CiJobIdentity, CiJobRow, CiStepRow, CiSummary } from '@/views/admin/ciActivityApi'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminBarChart from '@/components/admin/AdminBarChart.vue'
import AdminFlash from '@/components/admin/AdminFlash.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminLineChart from '@/components/admin/AdminLineChart.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { fmtNum } from '@/lib/usageFormat'

defineOptions({ name: 'AdminCiActivityPageView' })

/** Match the backend's 30-day retention window. */
const WINDOWS = [1, 7, 14, 30] as const

const props = withDefaults(
  defineProps<{
    board?: CiBoard | null
    days?: number
    loading?: boolean
    failed?: boolean
    jobs?: CiJobRow[]
    jobsLoading?: boolean
    /** Null selects all runners. */
    runner?: string | null
    steps?: CiStepRow[]
    stepsLoading?: boolean
    /** The job whose steps are expanded, or null. */
    job?: CiJobIdentity | null
  }>(),
  {
    board: null,
    days: 7,
    loading: false,
    failed: false,
    jobs: () => [],
    jobsLoading: false,
    runner: null,
    steps: () => [],
    stepsLoading: false,
    job: null,
  }
)

const emit = defineEmits<{
  setDays: [days: number]
  setRunner: [runner: string | null]
  setJob: [job: CiJobIdentity | null]
  retry: []
}>()

const { t } = useI18n()

const headline = computed(() => props.board?.headline ?? null)

function selectedJob(row: CiJobIdentity): boolean {
  return props.job?.name === row.name && props.job?.workflow_path === row.workflow_path
}

const windowOptions = computed(() =>
  WINDOWS.map((d) => ({ value: String(d), label: t('ciActivity.window.days', { n: d }) }))
)

/** Round without converting an absent sample to zero. */
const round1 = (v: number | null | undefined): number | null =>
  v === null || v === undefined ? null : Math.round(v * 10) / 10

function minutes(v: number | null | undefined): string {
  const r = round1(v)
  return r === null ? '' : t('ciActivity.unit.minutes', { n: fmtNum(r) })
}

/** Show short durations in seconds and longer ones in minutes. */
function seconds(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  if (v < 90) return t('ciActivity.unit.seconds', { n: fmtNum(Math.round(v)) })
  return minutes(v / 60)
}

function percent(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : `${fmtNum(Math.round(v * 1000) / 10)}%`
}

const dash = (s: string): string => s || '—'

/** Include sample count and distribution beside each headline. */
function spread(s: CiSummary | undefined, fmt: (v: number | null) => string): string {
  if (!s || s.n === 0) return t('ciActivity.note.noSample')
  return t('ciActivity.note.spread', {
    n: fmtNum(s.n),
    p50: dash(fmt(s.p50)),
    p90: dash(fmt(s.p90)),
    max: dash(fmt(s.max)),
  })
}

const kpis = computed(() => {
  const h = headline.value
  const share = h?.busy_runners.share
  return [
    {
      key: 'queue',
      label: t('ciActivity.kpi.queue'),
      value: minutes(h?.merge_queue_minutes.p50),
      note: `${t('ciActivity.note.queue')} ${spread(h?.merge_queue_minutes, minutes)}`,
    },
    {
      key: 'waitHosted',
      label: t('ciActivity.kpi.waitHosted'),
      value: seconds(h?.runner_wait_seconds.hosted.p50),
      note: `${t('ciActivity.note.wait')} ${spread(h?.runner_wait_seconds.hosted, seconds)}`,
    },
    {
      key: 'waitSelf',
      label: t('ciActivity.kpi.waitSelf'),
      value: seconds(h?.runner_wait_seconds.self_hosted.p50),
      note: `${t('ciActivity.note.wait')} ${spread(h?.runner_wait_seconds.self_hosted, seconds)}`,
    },
    {
      key: 'ciPr',
      label: t('ciActivity.kpi.ciPr'),
      value: minutes(h?.ci_required_minutes.pull_request.mean),
      note: `${t('ciActivity.note.ci')} ${spread(h?.ci_required_minutes.pull_request, minutes)}`,
    },
    {
      key: 'ciQueue',
      label: t('ciActivity.kpi.ciQueue'),
      value: minutes(h?.ci_required_minutes.merge_group.mean),
      note: `${t('ciActivity.note.ci')} ${spread(h?.ci_required_minutes.merge_group, minutes)}`,
    },
    {
      key: 'rounds',
      label: t('ciActivity.kpi.rounds'),
      value: h?.rounds.prs ? fmtNum(round1(h.rounds.pr_rounds.mean) ?? 0) : '',
      note: h?.rounds.prs
        ? t('ciActivity.note.rounds', {
            prs: fmtNum(h.rounds.prs),
            more: percent(h.rounds.more_than_one),
            queue: fmtNum(round1(h.rounds.queue_rounds.mean) ?? 0),
          })
        : t('ciActivity.note.noSample'),
    },
    {
      key: 'flaky',
      label: t('ciActivity.kpi.flaky'),
      value: percent(h?.fail_then_pass.share),
      note: t('ciActivity.note.flaky', {
        runs: fmtNum(h?.fail_then_pass.runs ?? 0),
        of: fmtNum(h?.fail_then_pass.of ?? 0),
      }),
    },
    {
      key: 'busy',
      label: t('ciActivity.kpi.busy'),
      value: h?.busy_runners.n ? fmtNum(round1(h.busy_runners.mean) ?? 0) : '',
      note: h?.busy_runners.n
        ? t('ciActivity.note.busy', {
            peak: fmtNum(h.busy_runners.max ?? 0),
            pr: dash(percent(share?.pr)),
            queue: dash(percent(share?.queue)),
            main: dash(percent(share?.main)),
          })
        : t('ciActivity.note.noSample'),
    },
  ]
})

const dayLabels = computed(() => (props.board?.daily ?? []).map((d) => d.day.slice(5)))

const queueSeries = computed<ChartSeries[]>(() => {
  const daily = props.board?.daily ?? []
  return [
    { name: 'p50', style: 'solid', values: daily.map((d) => round1(d.merge_queue_p50)) },
    { name: 'p90', style: 'dashed', values: daily.map((d) => round1(d.merge_queue_p90)) },
  ]
})

const ciSeries = computed<ChartSeries[]>(() => {
  const daily = props.board?.daily ?? []
  return [
    { name: t('ciActivity.series.pr'), style: 'solid', values: daily.map((d) => round1(d.ci_required_pr_p50)) },
    {
      name: t('ciActivity.series.queue'),
      style: 'dashed',
      values: daily.map((d) => round1(d.ci_required_queue_p50)),
    },
  ]
})

const waitSeries = computed<ChartSeries[]>(() => {
  const daily = props.board?.daily ?? []
  return [
    {
      name: t('ciActivity.series.hosted'),
      style: 'solid',
      values: daily.map((d) => round1(d.runner_wait_hosted_p50)),
    },
    {
      name: t('ciActivity.series.selfHosted'),
      style: 'dashed',
      values: daily.map((d) => round1(d.runner_wait_self_hosted_p50)),
    },
  ]
})

/** Keep the date when an hourly series spans multiple days. */
const hourLabels = computed(() =>
  (props.board?.hourly ?? []).map((h) => `${h.hour.slice(5, 10)} ${h.hour.slice(11, 13)}`)
)

const busySeries = computed<ChartSeries[]>(() => {
  const hourly = props.board?.hourly ?? []
  return [
    { name: t('ciActivity.series.busy'), style: 'solid', values: hourly.map((h) => round1(h.busy_mean)) },
    { name: t('ciActivity.series.pr'), style: 'dashed', values: hourly.map((h) => round1(h.pr_mean)) },
    { name: t('ciActivity.series.queue'), style: 'dotted', values: hourly.map((h) => round1(h.queue_mean)) },
  ]
})

const depthSeries = computed<ChartSeries[]>(() => [
  {
    name: t('ciActivity.series.depth'),
    style: 'solid',
    values: (props.board?.hourly ?? []).map((h) => h.queue_depth_peak),
  },
  {
    name: t('ciActivity.series.peak'),
    style: 'dashed',
    values: (props.board?.hourly ?? []).map((h) => h.busy_peak),
  },
])

const hourOfDayRows = computed(() =>
  (props.board?.hour_of_day ?? []).flatMap((h) => {
    const value = round1(h.busy_mean)
    return value === null
      ? []
      : [
          {
            id: String(h.hour),
            label: t('ciActivity.hourOfDay.label', { hour: String(h.hour).padStart(2, '0') }),
            value,
          },
        ]
  })
)

const ejectionRows = computed(() =>
  (props.board?.ejections ?? []).map((e) => ({
    id: `${e.reason} ${e.actor}`,
    label: t('ciActivity.ejections.label', { reason: e.reason, actor: e.actor }),
    value: e.count,
  }))
)

const roundRows = computed(() =>
  (props.board?.headline.rounds.histogram ?? []).map((r) => ({
    id: String(r.rounds),
    label: r.rounds >= 5 ? t('ciActivity.rounds.atLeast', { n: 5 }) : t('ciActivity.rounds.exactly', { n: r.rounds }),
    value: r.prs,
  }))
)

const sweepLine = computed(() => {
  const sweep = props.board?.sweep
  if (!sweep) return t('ciActivity.sweep.never')
  const at = `${sweep.at.slice(0, 16).replace('T', ' ')}Z`
  if (!sweep.complete && sweep.covered_from)
    return t('ciActivity.sweep.backfilling', {
      at,
      from: `${sweep.covered_from.slice(0, 16).replace('T', ' ')}Z`,
      owed: fmtNum(sweep.jobs_owed),
    })
  if (sweep.jobs_owed) return t('ciActivity.sweep.owed', { at, owed: fmtNum(sweep.jobs_owed) })
  return t('ciActivity.sweep.current', { at })
})

// The board is kept up to a minute per server worker, so it says when it was computed.
const computedLine = computed(() =>
  props.board ? t('ciActivity.computedAt', { at: `${props.board.generated_at.slice(0, 16).replace('T', ' ')}Z` }) : ''
)

const hostedLabel = (runner: string) => runner.replace(/^hosted: /, t('ciActivity.runners.hostedPrefix'))
</script>

<template>
  <AdminPage :title="t('navigation.admin.ciActivity')" :sub="t('ciActivity.page.subtitle')">
    <template #tools>
      <AdminTabs
        size="sm"
        :model-value="String(days)"
        :options="windowOptions"
        :label="t('ciActivity.window.aria')"
        @update:model-value="emit('setDays', Number($event))"
      />
    </template>

    <div class="aci__body admin-page__body">
      <BaseLoadError
        v-if="failed && !board"
        :title="t('ciActivity.state.loadFailed')"
        :retry-label="t('ciActivity.state.retry')"
        @retry="emit('retry')"
      />

      <template v-else>
        <p class="aci__prov t-meta-read">{{ sweepLine }}</p>
        <p v-if="board" class="aci__prov t-meta-read">{{ computedLine }}</p>
        <p v-if="headline?.rounds.incomplete_prs" class="aci__prov t-meta-read">
          {{
            headline.rounds.covered_from
              ? t('ciActivity.note.roundsIncomplete', {
                  n: fmtNum(headline.rounds.incomplete_prs),
                  from: headline.rounds.covered_from.slice(0, 16).replace('T', ' ') + 'Z',
                })
              : t('ciActivity.note.historyUnknown')
          }}
        </p>
        <AdminFlash
          v-if="board?.sweep?.error"
          tone="error"
          :text="t('ciActivity.sweep.error', { error: board.sweep.error })"
        />

        <div class="aci__kpis">
          <AdminKpiCard
            v-for="k in kpis"
            :key="k.key"
            :label="k.label"
            :value="k.value"
            :note="k.note"
            :loading="loading && !board"
          />
        </div>

        <div class="aci__charts">
          <AdminLineChart
            :title="t('ciActivity.chart.queue')"
            :note="t('ciActivity.note.queue')"
            :x-labels="dayLabels"
            :series="queueSeries"
            :loading="loading && !board"
          />
          <AdminLineChart
            :title="t('ciActivity.chart.ci')"
            :note="t('ciActivity.note.ci')"
            :x-labels="dayLabels"
            :series="ciSeries"
            :loading="loading && !board"
          />
          <AdminLineChart
            :title="t('ciActivity.chart.wait')"
            :note="t('ciActivity.note.wait')"
            :x-labels="dayLabels"
            :series="waitSeries"
            :loading="loading && !board"
          />
          <AdminLineChart
            :title="t('ciActivity.chart.busy')"
            :note="t('ciActivity.note.busyChart')"
            :x-labels="hourLabels"
            :series="busySeries"
            :loading="loading && !board"
          />
          <AdminLineChart
            :title="t('ciActivity.chart.depth')"
            :note="t('ciActivity.note.depth')"
            :x-labels="hourLabels"
            :series="depthSeries"
            :loading="loading && !board"
          />
          <AdminBarChart :title="t('ciActivity.chart.hourOfDay')" :rows="hourOfDayRows" :loading="loading && !board" />
          <AdminBarChart :title="t('ciActivity.chart.rounds')" :rows="roundRows" :loading="loading && !board" />
          <AdminBarChart :title="t('ciActivity.chart.ejections')" :rows="ejectionRows" :loading="loading && !board" />
        </div>

        <section class="aci__block">
          <h2 class="aci__block-title">{{ t('ciActivity.runners.title') }}</h2>
          <BaseEmptyState v-if="board && !board.runners.length" size="inline" :title="t('ciActivity.runners.empty')" />
          <table v-else class="aci__table">
            <thead>
              <tr>
                <th scope="col">{{ t('ciActivity.runners.col.runner') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.jobs') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.busy') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.utilisation') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.wait') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.duration') }}</th>
                <th scope="col">{{ t('ciActivity.runners.col.failed') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in board?.runners ?? []" :key="r.runner" :class="{ 'aci__row--on': runner === r.runner }">
                <td>
                  <button
                    type="button"
                    class="aci__pick"
                    :aria-pressed="runner === r.runner"
                    @click="emit('setRunner', runner === r.runner ? null : r.runner)"
                  >
                    {{ hostedLabel(r.runner) }}
                  </button>
                </td>
                <td class="t-num">{{ fmtNum(r.jobs) }}</td>
                <td class="t-num">{{ fmtNum(round1(r.busy_hours) ?? 0) }}</td>
                <td class="t-num">{{ dash(percent(r.utilisation)) }}</td>
                <td class="t-num">{{ dash(seconds(r.wait_p50_s)) }}</td>
                <td class="t-num">{{ dash(seconds(r.duration_p50_s)) }}</td>
                <td class="t-num">{{ fmtNum(r.failed) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="aci__block">
          <h2 class="aci__block-title">
            {{ runner ? t('ciActivity.jobs.titleOn', { runner: hostedLabel(runner) }) : t('ciActivity.jobs.title') }}
            <button v-if="runner" type="button" class="aci__clear" @click="emit('setRunner', null)">
              {{ t('ciActivity.jobs.allRunners') }}
            </button>
          </h2>
          <p v-if="jobsLoading && !jobs.length" class="aci__note t-meta-read">{{ t('ciActivity.state.loading') }}</p>
          <BaseEmptyState v-else-if="!jobs.length" size="inline" :title="t('ciActivity.jobs.empty')" />
          <table v-else class="aci__table" :class="{ 'aci__table--busy': jobsLoading }">
            <thead>
              <tr>
                <th scope="col">{{ t('ciActivity.jobs.col.job') }}</th>
                <th scope="col">{{ t('ciActivity.jobs.col.runs') }}</th>
                <th scope="col">p50</th>
                <th scope="col">p90</th>
                <th scope="col">{{ t('ciActivity.jobs.col.max') }}</th>
                <th scope="col">{{ t('ciActivity.jobs.col.wait') }}</th>
                <th scope="col">{{ t('ciActivity.jobs.col.failed') }}</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="j in jobs" :key="JSON.stringify([j.workflow_path, j.name])">
                <tr :class="{ 'aci__row--on': selectedJob(j) }">
                  <td>
                    <button
                      type="button"
                      class="aci__pick"
                      :aria-expanded="selectedJob(j)"
                      @click="emit('setJob', selectedJob(j) ? null : { name: j.name, workflow_path: j.workflow_path })"
                    >
                      {{ j.name }}
                    </button>
                    <span class="aci__sub t-meta">{{ j.workflow }}</span>
                  </td>
                  <td class="t-num">{{ fmtNum(j.jobs) }}</td>
                  <td class="t-num">{{ dash(seconds(j.duration_s.p50)) }}</td>
                  <td class="t-num">{{ dash(seconds(j.duration_s.p90)) }}</td>
                  <td class="t-num">{{ dash(seconds(j.duration_s.max)) }}</td>
                  <td class="t-num">{{ dash(seconds(j.wait_p50_s)) }}</td>
                  <td class="t-num">{{ fmtNum(j.failed) }}</td>
                </tr>
                <tr v-if="selectedJob(j)">
                  <td colspan="7" class="aci__steps">
                    <p v-if="stepsLoading && !steps.length" class="t-meta-read">{{ t('ciActivity.state.loading') }}</p>
                    <p v-else-if="!steps.length" class="t-meta-read">{{ t('ciActivity.steps.empty') }}</p>
                    <table v-else class="aci__table aci__table--inner">
                      <thead>
                        <tr>
                          <th scope="col">{{ t('ciActivity.steps.col.step') }}</th>
                          <th scope="col">{{ t('ciActivity.jobs.col.runs') }}</th>
                          <th scope="col">p50</th>
                          <th scope="col">p90</th>
                          <th scope="col">{{ t('ciActivity.jobs.col.max') }}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="s in steps" :key="s.name">
                          <td>{{ s.name }}</td>
                          <td class="t-num">{{ fmtNum(s.duration_s.n) }}</td>
                          <td class="t-num">{{ dash(seconds(s.duration_s.p50)) }}</td>
                          <td class="t-num">{{ dash(seconds(s.duration_s.p90)) }}</td>
                          <td class="t-num">{{ dash(seconds(s.duration_s.max)) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </section>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
.aci__body {
  padding-top: 0;
}

.aci__prov {
  margin: 16px 0 0;
}

.aci__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  margin: 16px 0 0;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.aci__charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
  gap: 16px;
  margin-top: 16px;
}

.aci__block {
  margin-top: 24px;
}

.aci__block-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.aci__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.aci__table th,
.aci__table td {
  padding: 6px 8px;
  text-align: right;
  border-bottom: 1px solid var(--line);
}

.aci__table th:first-child,
.aci__table td:first-child {
  text-align: left;
}

.aci__table th {
  color: var(--muted);
  font-weight: 500;
}

.aci__table--busy {
  opacity: 0.6;
}

.aci__table--inner {
  font-size: 13px;
}

.aci__row--on > td {
  background: var(--raised);
}

.aci__pick,
.aci__clear {
  padding: 0;
  color: var(--text);
  font: inherit;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.aci__pick:hover,
.aci__clear:hover {
  text-decoration: underline;
}

.aci__clear {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
}

.aci__sub {
  display: block;
  color: var(--muted);
}

.aci__steps {
  padding: 4px 0 12px 16px;
}

.aci__note {
  margin: 0;
}

@media (max-width: 600px) {
  .aci__table {
    display: block;
    overflow-x: auto;
  }
}
</style>
