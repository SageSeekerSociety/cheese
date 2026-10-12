<script setup lang="ts">
/**
 * 后台「机器」这一屏的画面：只收结果，不取数。路由页 `AdminMachinesPage.vue` 取数。
 *
 * 从上到下回答四个问题：池子此刻够不够用（四张卡，外加「此刻在拒绝新会话」那一句）；
 * 每台宿主机走到哪一步、上面有谁（一台一行，点开列出它的沙箱）；过去一天沙箱起得
 * 多快、有多少没起来、拒了多少会话、丢了几台宿主机；温机池里有什么。
 */
import type { HostPhase, MachineHost, MachineSandbox, MachinesOverview, SandboxState } from '@/views/admin/machinesApi'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { dayStamp } from '@/lib/dateText'

defineOptions({ name: 'AdminMachinesPageView' })

const props = withDefaults(
  defineProps<{
    overview?: MachinesOverview | null
    loading?: boolean
    failed?: boolean
  }>(),
  { overview: null, loading: false, failed: false }
)

const emit = defineEmits<{ retry: [] }>()

const { t, n } = useI18n()

// 键名写全，不拼。
const PHASE_LABEL: Record<HostPhase, string> = {
  creating: 'admin.machines.phase.creating',
  enrolling: 'admin.machines.phase.enrolling',
  enroll_failing: 'admin.machines.phase.enrollFailing',
  ready: 'admin.machines.phase.ready',
  idle: 'admin.machines.phase.idle',
  offline: 'admin.machines.phase.offline',
  error: 'admin.machines.phase.error',
  releasing: 'admin.machines.phase.releasing',
  failed: 'admin.machines.phase.failed',
}
const STATE_LABEL: Record<SandboxState, string> = {
  waiting: 'admin.machines.state.waiting',
  working: 'admin.machines.state.working',
  idle: 'admin.machines.state.idle',
  stopping: 'admin.machines.state.stopping',
}

/** 圆点：坏了的红、要留意的黄、正常的绿、走完了或还没开始的灰。 */
function phaseDot(phase: HostPhase): string {
  if (phase === 'error' || phase === 'failed' || phase === 'enroll_failing') return 'status-dot--danger'
  if (phase === 'offline') return 'status-dot--warn'
  if (phase === 'ready') return 'status-dot--ok'
  return 'status-dot--muted'
}
function stateDot(state: SandboxState): string {
  if (state === 'waiting') return 'status-dot--warn'
  if (state === 'working') return 'status-dot--ok'
  return 'status-dot--muted'
}

const nowMs = computed(() => (props.overview ? new Date(props.overview.now).getTime() : Date.now()))

/** 一段时长：45 秒、12 分钟、3.5 小时、2 天。 */
function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  const s = Math.max(0, seconds)
  if (s < 60) return t('admin.machines.dur.s', { n: Math.round(s) })
  if (s < 3600) return t('admin.machines.dur.m', { n: Math.round(s / 60) })
  if (s < 86400) return t('admin.machines.dur.h', { n: Math.round((s / 3600) * 10) / 10 })
  return t('admin.machines.dur.d', { n: Math.round((s / 86400) * 10) / 10 })
}
function since(iso: string | null): string {
  return iso ? duration((nowMs.value - new Date(iso).getTime()) / 1000) : ''
}
function until(iso: string | null): string {
  return iso ? duration((new Date(iso).getTime() - nowMs.value) / 1000) : ''
}
function stamp(iso: string | null): string {
  return iso ? dayStamp(new Date(iso).getTime(), nowMs.value) : ''
}

function spec(h: MachineHost): string {
  return t('admin.machines.spec', { cores: h.cores, memory: Math.round(h.memory_mb / 1024), disk: h.disk_gb })
}

/** 这一台此刻最该让人知道的一句：为什么坏、从什么时候起。 */
function hostNote(h: MachineHost): string {
  if (h.phase === 'failed') return t('admin.machines.note.failed', { at: stamp(h.failed_at) })
  if (h.phase === 'releasing') return t('admin.machines.note.releasing', { at: stamp(h.released_at) })
  if (h.phase === 'enroll_failing')
    return t('admin.machines.note.enrollFailing', { n: h.enroll_attempts, error: h.enroll_error ?? '' })
  if (h.phase === 'offline' && h.offline_since) return t('admin.machines.note.offline', { for: since(h.offline_since) })
  if (h.phase === 'idle' && h.idle_since) return t('admin.machines.note.idle', { for: since(h.idle_since) })
  if (h.whole_machine) return t('admin.machines.note.vm', { project: h.project ?? '—' })
  return ''
}

function sandboxWhen(s: MachineSandbox): string {
  if (s.state === 'waiting') return t('admin.machines.when.waiting', { for: since(s.waiting_since) })
  if (s.state === 'stopping') return t('admin.machines.when.stopping', { for: since(s.stopped_at) })
  const active = t('admin.machines.when.active', { ago: since(s.active_at) })
  if (s.state === 'idle' && s.idle_deadline) {
    const due = new Date(s.idle_deadline).getTime() <= nowMs.value
    const after = due
      ? t('admin.machines.when.overdue')
      : t('admin.machines.when.reclaim', { in: until(s.idle_deadline) })
    return `${active} · ${after}`
  }
  return active
}

// 温机的状态是后端的代号；认不出的照原样画，不替它编一个词。
const WARM_LABEL: Record<string, string> = {
  preparing: 'admin.machines.warmState.preparing',
  ready: 'admin.machines.warmState.ready',
  reserved: 'admin.machines.warmState.reserved',
  claim_failed: 'admin.machines.warmState.claimFailed',
  deleting: 'admin.machines.warmState.deleting',
  cleanup_failed: 'admin.machines.warmState.cleanupFailed',
}
function warmState(state: string): string {
  const key = WARM_LABEL[state]
  return key ? t(key) : state
}

const open = ref<Set<string>>(new Set())
function toggle(id: string) {
  const next = new Set(open.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  open.value = next
}

const pool = computed(() => props.overview?.pool ?? null)
const hosts = computed(() => props.overview?.hosts ?? [])
const history = computed(() => props.overview?.history ?? null)
const first = computed(() => props.loading && !props.overview)

const hostsNote = computed(() =>
  pool.value
    ? [t('admin.machines.kpi.hostsNote'), pool.value.vms ? t('admin.machines.kpi.vms', { n: pool.value.vms }) : '']
        .filter(Boolean)
        .join(' ')
    : ''
)

const failureNote = computed(() =>
  pool.value
    ? t('admin.machines.kpi.failuresNote', {
        limit: pool.value.provider_failure_limit,
        minutes: pool.value.provider_window_min,
        probe: pool.value.provider_probe_min,
      })
    : ''
)
</script>

<template>
  <AdminPage :title="t('navigation.admin.machines')" :sub="t('admin.machines.sub')">
    <template #extra>
      <div class="mc-kpis">
        <AdminKpiCard
          :label="t('admin.machines.kpi.hosts')"
          :value="pool ? `${n(pool.hosts + pool.vms)} / ${n(pool.max_hosts)}` : ''"
          :loading="first"
          :note="pool ? hostsNote : undefined"
        />
        <AdminKpiCard
          :label="t('admin.machines.kpi.slots')"
          :value="pool ? `${n(pool.used)} / ${n(pool.slots)}` : ''"
          :loading="first"
          :note="pool ? t('admin.machines.kpi.slotsNote', { free: pool.free, min: pool.min_free }) : undefined"
        />
        <AdminKpiCard
          :label="t('admin.machines.kpi.waiting')"
          :value="pool ? n(pool.waiting) : ''"
          :loading="first"
          :note="
            pool && pool.oldest_wait_since
              ? t('admin.machines.kpi.waitingNote', { for: since(pool.oldest_wait_since) })
              : undefined
          "
        />
        <AdminKpiCard
          :label="t('admin.machines.kpi.failures')"
          :value="pool ? n(pool.provider_failures) : ''"
          :loading="first"
          :note="failureNote"
        />
      </div>
      <div v-if="overview" class="mc-alerts">
        <p v-if="!overview.configured" class="mc-alert">
          <span class="status-dot status-dot--muted" />
          {{ t('admin.machines.alert.notConfigured') }}
        </p>
        <p v-if="pool?.refusing" class="mc-alert">
          <span class="status-dot status-dot--danger" />
          {{
            t('admin.machines.alert.refusing', {
              n: pool.provider_failures,
              at: stamp(pool.last_provider_failure),
            })
          }}
        </p>
        <p v-if="pool?.at_cap" class="mc-alert">
          <span class="status-dot status-dot--warn" />
          {{ t('admin.machines.alert.atCap', { max: pool.max_hosts }) }}
        </p>
      </div>
    </template>

    <BaseLoadError v-if="failed && !overview" @retry="emit('retry')" />
    <div v-else-if="overview" class="admin-page__body">
      <section class="mc-section">
        <h2 class="t-title">{{ t('admin.machines.hosts') }}</h2>
        <BaseEmptyState v-if="!hosts.length" :title="t('admin.machines.noHosts')" />
        <div v-else class="mc-table" role="table" :aria-label="t('admin.machines.hosts')">
          <div class="mc-row mc-row--head" role="row">
            <span role="columnheader" />
            <span role="columnheader">{{ t('admin.machines.col.host') }}</span>
            <span role="columnheader">{{ t('admin.machines.col.phase') }}</span>
            <span role="columnheader">{{ t('admin.machines.col.spec') }}</span>
            <span role="columnheader">{{ t('admin.machines.col.slots') }}</span>
            <span role="columnheader">{{ t('admin.machines.col.age') }}</span>
            <span role="columnheader">{{ t('admin.machines.col.note') }}</span>
          </div>
          <template v-for="h in hosts" :key="h.id">
            <button
              type="button"
              role="row"
              class="mc-row"
              :class="{ 'mc-row--gone': h.phase === 'failed' || h.phase === 'releasing' }"
              :aria-expanded="open.has(h.id)"
              :disabled="!h.sandboxes.length"
              @click="toggle(h.id)"
            >
              <v-icon size="16" class="mc-caret" :class="{ 'mc-caret--hidden': !h.sandboxes.length }">
                {{ open.has(h.id) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
              </v-icon>
              <span class="mc-host">
                <span class="mc-mono c-ink">{{ h.hostname }}</span>
                <span v-if="h.machine_id !== null" class="t-meta">#{{ h.machine_id }}</span>
                <span v-if="h.whole_machine" class="chip-neutral">{{ t('admin.machines.vm') }}</span>
              </span>
              <span class="mc-phase">
                <span class="status-dot" :class="phaseDot(h.phase)" />
                {{ t(PHASE_LABEL[h.phase]) }}
              </span>
              <span class="mc-dim mc-spec">{{ spec(h) }}</span>
              <span class="mc-slots">
                <template v-if="!h.whole_machine">
                  <span class="mc-meter" aria-hidden="true">
                    <i :style="{ width: `${h.slots ? Math.min(100, (100 * h.used) / h.slots) : 0}%` }" />
                  </span>
                  <span class="mc-num">{{ n(h.used) }} / {{ n(h.slots) }}</span>
                </template>
                <span v-else class="mc-num">{{ n(h.used) }}</span>
              </span>
              <span class="mc-dim mc-num mc-age" :title="stamp(h.created_at)">{{ since(h.created_at) }}</span>
              <span class="mc-note" :title="hostNote(h)">{{ hostNote(h) }}</span>
            </button>
            <div v-if="open.has(h.id)" class="mc-sandboxes" role="rowgroup">
              <!-- 和宿主机那一行同一套列：房间落在「宿主机」列下，状态落在「阶段」列下。 -->
              <div v-for="s in h.sandboxes" :key="s.id" class="mc-row mc-row--sandbox" role="row">
                <span class="mc-caret" />
                <a class="mc-place" :href="`/projects/${s.project_id}/topics/${s.room_id}`">
                  {{ s.project ?? '—' }} / {{ s.room ?? t('admin.machines.taskRoom') }}
                </a>
                <span class="mc-phase">
                  <span class="status-dot" :class="stateDot(s.state)" />
                  {{ t(STATE_LABEL[s.state]) }}
                </span>
                <span class="mc-dim mc-when">{{ sandboxWhen(s) }}</span>
                <span class="mc-dim mc-placed">{{ t('admin.machines.placed', { at: stamp(s.placed_at) }) }}</span>
              </div>
            </div>
          </template>
        </div>
      </section>

      <section v-if="history" class="mc-section">
        <h2 class="t-title">{{ t('admin.machines.history') }}</h2>
        <dl class="mc-facts">
          <div>
            <dt>{{ t('admin.machines.fact.prepared') }}</dt>
            <dd class="mc-num">{{ n(history.prepared) }}</dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.ready') }}</dt>
            <dd class="mc-num">{{ n(history.ready) }}</dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.wait') }}</dt>
            <dd class="mc-num">
              {{
                history.wait_p50_s === null
                  ? '—'
                  : t('admin.machines.fact.waitValue', {
                      p50: duration(history.wait_p50_s),
                      p95: duration(history.wait_p95_s),
                      max: duration(history.wait_max_s),
                    })
              }}
            </dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.abandoned') }}</dt>
            <dd class="mc-num">{{ n(history.abandoned) }}</dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.refused') }}</dt>
            <dd class="mc-num">
              {{
                t('admin.machines.fact.refusedValue', {
                  sessions: history.refused_sessions,
                  full: history.refused_by_reason.pool_full ?? 0,
                  failing: history.refused_by_reason.provider_failing ?? 0,
                })
              }}
            </dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.hostsLost') }}</dt>
            <dd class="mc-num">
              {{
                t('admin.machines.fact.hostsLostValue', {
                  n: history.hosts_lost,
                  error: history.hosts_lost_by_reason.provider_error ?? 0,
                  away: history.hosts_lost_by_reason.connector_away ?? 0,
                  never: history.hosts_lost_by_reason.never_enrolled ?? 0,
                })
              }}
            </dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.sandboxesLost') }}</dt>
            <dd class="mc-num">{{ n(history.sandboxes_lost) }}</dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.replaced') }}</dt>
            <dd class="mc-num">{{ n(history.replaced) }}</dd>
          </div>
          <div>
            <dt>{{ t('admin.machines.fact.released') }}</dt>
            <dd class="mc-num">
              {{
                t('admin.machines.fact.releasedValue', { idle: history.released_idle, unpaid: history.released_unpaid })
              }}
            </dd>
          </div>
        </dl>
      </section>

      <section class="mc-section">
        <h2 class="t-title">{{ t('admin.machines.warm', { target: pool?.warm_target ?? 0 }) }}</h2>
        <p v-if="!overview.warm.length" class="mc-dim">{{ t('admin.machines.noWarm') }}</p>
        <ul v-else class="mc-warm">
          <li v-for="w in overview.warm" :key="w.id" class="mc-warm__row">
            <span class="mc-phase">
              <span
                class="status-dot"
                :class="w.error ? 'status-dot--danger' : w.state === 'ready' ? 'status-dot--ok' : 'status-dot--muted'"
              />
              <span>{{ warmState(w.state) }}</span>
            </span>
            <span v-if="w.machine_id !== null" class="t-meta">#{{ w.machine_id }}</span>
            <span class="mc-dim"
              >{{ t('admin.machines.warmAge', { for: since(w.created_at) }) }} ·
              {{ t('admin.machines.attempts', { n: w.attempts }, w.attempts) }}</span
            >
            <span v-if="w.error" class="mc-note" :title="w.error">{{ w.error }}</span>
          </li>
        </ul>
      </section>

      <p class="mc-foot">
        {{
          t('admin.machines.config', {
            perCore: overview.config.slots_per_core,
            memory: Math.round(overview.config.sandbox_memory_mb / 1024),
            sandboxIdle: duration(overview.config.sandbox_idle_s),
            hostIdle: duration(overview.config.host_idle_hold_s),
            vmIdle: duration(overview.config.vm_idle_s),
          })
        }}
      </p>
    </div>
  </AdminPage>
</template>

<style scoped>
.mc-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}
.mc-alerts:empty {
  display: none;
}
.mc-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 12px 0 0;
  font-size: 14px;
  color: var(--ink);
}
/* 一句话折成几行时，圆点跟着第一行。 */
.mc-alert .status-dot {
  flex: none;
  margin-top: 0.55em;
}
.mc-section {
  margin-top: 28px;
}
.mc-section > .t-title {
  margin: 0 0 8px;
}
.mc-table {
  overflow-x: auto;
}
/* 宿主机那一行、它展开的沙箱行、表头共用这一套列：沙箱的房间和状态落在「宿主机」
   「阶段」两列下面。 */
.mc-row {
  display: grid;
  grid-template-columns: 16px minmax(160px, 1.2fr) 112px 140px 104px 76px minmax(160px, 1.6fr);
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 840px;
  padding: 8px 12px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: none;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.mc-row:hover:not(:disabled) {
  background: var(--fill);
}
.mc-row:disabled {
  cursor: default;
}
.mc-row--head {
  font-size: 12px;
  color: var(--faint);
  cursor: default;
}
.mc-row--gone {
  color: var(--muted);
}
.mc-row--sandbox {
  padding-top: 6px;
  padding-bottom: 6px;
  border-bottom: 0;
  cursor: default;
}
.mc-when {
  grid-column: 4 / 7;
}
.mc-caret {
  color: var(--faint);
}
.mc-caret--hidden {
  visibility: hidden;
}
.mc-host {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.mc-phase {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.mc-mono {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-dim {
  color: var(--muted);
}
.mc-num {
  font-variant-numeric: tabular-nums;
}
.mc-age {
  white-space: nowrap;
}
.mc-slots {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mc-meter {
  position: relative;
  flex: 0 0 48px;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
}
.mc-meter i {
  position: absolute;
  inset: 0 auto 0 0;
  background: var(--muted);
}
.mc-note {
  color: var(--muted);
  overflow-wrap: anywhere;
}
.mc-sandboxes {
  padding: 2px 0 6px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}
.mc-place {
  overflow: hidden;
  color: var(--text);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-place:hover {
  text-decoration: underline;
}
.mc-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 24px;
  margin: 0;
}
.mc-facts dt {
  font-size: 12px;
  color: var(--faint);
}
.mc-facts dd {
  margin: 2px 0 0;
  font-size: 14px;
  color: var(--ink);
}
/* 口径说明是一句话，不是时间戳或计数：用正文字体，只降一档颜色和字号。 */
.mc-foot {
  margin: 12px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--faint);
}
.mc-warm {
  margin: 0;
  padding: 0;
  list-style: none;
}
.mc-warm__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}
/* 窄屏放不下七列：一行折成三行（名字和阶段 / 规格和沙箱 / 说明），表头不画，不靠
   横向滚动把一半的列藏到屏幕外。沙箱行同理：房间和状态 / 什么时候 / 放上来的时刻。 */
@media (max-width: 760px) {
  .mc-row {
    grid-template-columns: 16px minmax(0, 1fr) auto;
    grid-template-areas:
      'caret host phase'
      '. spec slots'
      '. note note';
    row-gap: 4px;
    min-width: 0;
  }
  .mc-row--head,
  .mc-age {
    display: none;
  }
  .mc-caret {
    grid-area: caret;
  }
  .mc-host,
  .mc-place {
    grid-area: host;
  }
  .mc-phase {
    grid-area: phase;
  }
  .mc-spec,
  .mc-when {
    grid-area: spec;
  }
  .mc-slots {
    grid-area: slots;
  }
  .mc-note,
  .mc-placed {
    grid-area: note;
  }
  .mc-row--sandbox {
    grid-template-areas:
      'caret host phase'
      '. spec spec'
      '. note note';
  }
}
</style>
