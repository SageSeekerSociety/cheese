<script setup lang="ts">
/**
 * 后台「机器」这一屏的画面：只收结果，不取数。路由页 `AdminMachinesPage.vue` 取数。
 *
 * 从上到下回答四个问题：池子此刻够不够用（四张卡，外加「此刻在拒绝新会话」那一句）；
 * 每台宿主机走到哪一步、上面有谁、磁盘内存 CPU 用了多少（一台一行，下面一条用量，
 * 点开列出它的沙箱和每个沙箱离上限多远；还没被领走的温机也各占一行，在跑就在花钱，
 * 不能看不见）；过去一天沙箱起得多快、有多少没起来、拒了多少会话、丢了几台宿主机。
 *
 * 用量是磁盘巡检上次读到的，不是这一刻：读数多旧写在旁边，超过三次巡检没读到就标黄。
 */
import type {
  HostPhase,
  HostUsage,
  MachineHost,
  MachineSandbox,
  MachinesOverview,
  SandboxState,
  WarmMachineRow,
} from '@/views/admin/machinesApi'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { dayStamp } from '@/lib/dateText'
import { fmtCompact, fmtPercent } from '@/lib/usageFormat'

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

function spec(h: MachineHost | WarmMachineRow): string {
  if (h.cores === null || h.memory_mb === null || h.disk_gb === null) return ''
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

// 磁盘按十进制 GB（和 12 GB 的沙箱上限同一把尺子），内存按 GiB（和 3 GiB 的上限同一把）。
function gb(bytes: number): string {
  return (bytes / 1e9).toFixed(1)
}
function gib(bytes: number): string {
  return (bytes / 2 ** 30).toFixed(1)
}
function ratio(used: number | null, total: number | null): number | null {
  return used === null || !total ? null : used / total
}

/** 读数旧过三次巡检：宿主机多半没在被读，画面上的数不能当此刻看。 */
function stale(at: string | null): boolean {
  if (!at || !props.overview) return false
  return (nowMs.value - new Date(at).getTime()) / 1000 > 3 * props.overview.config.check_s
}

type MeterKey = 'disk' | 'inodes' | 'memory' | 'cpu'
type Meter = { key: MeterKey; label: string; value: string; ratio: number; hint: string; tone: 'ink' | 'warn' }
const METER_LABEL: Record<MeterKey, string> = {
  disk: 'admin.machines.usage.disk',
  inodes: 'admin.machines.usage.inodes',
  memory: 'admin.machines.usage.memory',
  cpu: 'admin.machines.usage.cpu',
}

/** 一台宿主机的四根条：过了告警线（`alert_used`，和告警同一条线）的那根标黄。 */
function meters(u: HostUsage): Meter[] {
  const line = props.overview?.config.alert_used
  const out: Meter[] = []
  const add = (key: MeterKey, share: number | null, value: string, hint?: string, alertAt?: number) => {
    out.push({
      key,
      label: t(METER_LABEL[key]),
      value: share === null ? '—' : value,
      ratio: share ?? 0,
      hint: hint ?? fmtPercent(share, 0),
      tone: share !== null && alertAt !== undefined && share > alertAt ? 'warn' : 'ink',
    })
  }
  add(
    'disk',
    ratio(u.disk_used_bytes, u.disk_bytes),
    t('admin.machines.usage.gb', { used: gb(u.disk_used_bytes), total: gb(u.disk_bytes) }),
    undefined,
    line?.disk
  )
  add(
    'inodes',
    ratio(u.inodes_used, u.inodes),
    `${fmtCompact(u.inodes_used)} / ${fmtCompact(u.inodes)}`,
    undefined,
    line?.inodes
  )
  add(
    'memory',
    ratio(u.memory_used_bytes, u.memory_bytes),
    t('admin.machines.usage.gib', { used: gib(u.memory_used_bytes ?? 0), total: gib(u.memory_bytes ?? 0) }),
    undefined,
    line?.memory
  )
  add(
    'cpu',
    u.cpu_busy,
    fmtPercent(u.cpu_busy, 0),
    u.load1 === null ? '' : t('admin.machines.usage.load', { n: u.load1 })
  )
  return out
}

/** 一个沙箱离它的上限多远：内存对 `sandbox_memory_mb`，磁盘对 `sandbox_disk_budget_gb`
 * 和文件数上限；到 `near_cap` 这一份就标出来。 */
function sandboxUse(s: MachineSandbox): { text: string; near: boolean }[] {
  const u = s.usage
  const c = props.overview?.config
  if (!u || !c) return [{ text: t('admin.machines.usage.none'), near: false }]
  const parts: { text: string; near: boolean }[] = []
  if (u.memory_bytes !== null) {
    const cap = c.sandbox_memory_mb * 2 ** 20
    const peak = u.memory_peak_bytes
    parts.push({
      text:
        peak === null
          ? t('admin.machines.usage.sandboxMemory', { used: gib(u.memory_bytes), cap: gib(cap) })
          : t('admin.machines.usage.sandboxMemoryPeak', { used: gib(u.memory_bytes), cap: gib(cap), peak: gib(peak) }),
      near: Math.max(u.memory_bytes, peak ?? 0) >= c.near_cap * cap,
    })
  }
  if (u.disk_bytes !== null) {
    parts.push({
      text: t('admin.machines.usage.sandboxDisk', { used: gb(u.disk_bytes), cap: c.sandbox_disk_budget_gb }),
      near: u.disk_bytes >= c.near_cap * c.sandbox_disk_budget_gb * 1e9,
    })
  }
  if (u.disk_inodes !== null) {
    parts.push({
      text: t('admin.machines.usage.sandboxFiles', {
        used: fmtCompact(u.disk_inodes),
        cap: fmtCompact(c.sandbox_inode_budget),
      }),
      near: u.disk_inodes >= c.near_cap * c.sandbox_inode_budget,
    })
  }
  return parts.length ? parts : [{ text: t('admin.machines.usage.none'), near: false }]
}
function sandboxUseTitle(s: MachineSandbox): string {
  if (!s.usage) return ''
  return t('admin.machines.usage.sandboxRead', {
    memory: s.usage.memory_read_at ? t('admin.machines.usage.ago', { ago: since(s.usage.memory_read_at) }) : '—',
    disk: s.usage.disk_read_at ? t('admin.machines.usage.ago', { ago: since(s.usage.disk_read_at) }) : '—',
  })
}

function showsUsage(h: MachineHost): boolean {
  return h.usage !== null && h.phase !== 'failed' && h.phase !== 'releasing'
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
const warm = computed(() => props.overview?.warm ?? [])
const history = computed(() => props.overview?.history ?? null)
const first = computed(() => props.loading && !props.overview)
/** 小折线画的是多长一段：步长乘点数，后端说了算。 */
const historySpan = computed(() => {
  const read = hosts.value.find((h) => h.usage)?.usage
  return read && props.overview ? duration(props.overview.config.history_step_s * read.memory_history.length) : ''
})

const hostsNote = computed(() =>
  pool.value
    ? [
        t('admin.machines.kpi.hostsNote'),
        pool.value.vms ? t('admin.machines.kpi.vms', { n: pool.value.vms }) : '',
        t('admin.machines.kpi.warm', { n: warm.value.length, target: pool.value.warm_target }),
      ]
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
        <BaseEmptyState v-if="!hosts.length && !warm.length" :title="t('admin.machines.noHosts')" />
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
            <div
              v-if="h.usage && showsUsage(h)"
              class="mc-usage"
              role="group"
              :aria-label="t('admin.machines.usage.label', { host: h.hostname })"
            >
              <div v-for="m in meters(h.usage)" :key="m.key" class="mc-usage__meter">
                <AdminMeterBar
                  :label="m.label"
                  :value-text="m.value"
                  :limit="1"
                  :ratio="m.ratio"
                  :tone="m.tone"
                  :hint="m.hint"
                />
                <AdminSparkline v-if="m.key === 'memory'" :values="h.usage.memory_history" :height="16" />
                <AdminSparkline v-else-if="m.key === 'cpu'" :values="h.usage.cpu_history" :height="16" />
              </div>
              <p class="mc-usage__read" :class="{ 'mc-usage__read--stale': stale(h.usage.read_at) }">
                <span v-if="stale(h.usage.read_at)" class="status-dot status-dot--warn" />
                {{
                  stale(h.usage.read_at)
                    ? t('admin.machines.usage.readStale', { ago: since(h.usage.read_at) })
                    : t('admin.machines.usage.read', { ago: since(h.usage.read_at) })
                }}
                <template v-if="historySpan">
                  · {{ t('admin.machines.usage.history', { span: historySpan }) }}</template
                >
              </p>
            </div>
            <div v-if="open.has(h.id)" class="mc-sandboxes" role="rowgroup">
              <!-- 和宿主机那一行同一套列：房间落在「宿主机」列下，状态落在「阶段」列下。 -->
              <div v-for="s in h.sandboxes" :key="s.id" class="mc-row mc-row--sandbox" role="row">
                <span class="mc-caret" />
                <a v-if="s.project_id" class="mc-place" :href="`/projects/${s.project_id}/topics/${s.room_id}`">
                  {{ s.project ?? '—' }} / {{ s.room ?? t('admin.machines.taskRoom') }}
                </a>
                <!-- A person's own conversation is theirs alone: named, not linked. -->
                <span v-else class="mc-place">{{ t('admin.machines.personal') }}</span>
                <span class="mc-phase">
                  <span class="status-dot" :class="stateDot(s.state)" />
                  {{ t(STATE_LABEL[s.state]) }}
                </span>
                <span class="mc-dim mc-when">{{ sandboxWhen(s) }}</span>
                <span class="mc-dim mc-placed">{{ t('admin.machines.placed', { at: stamp(s.placed_at) }) }}</span>
                <span class="mc-use" :title="sandboxUseTitle(s)">
                  <span
                    v-for="(part, i) in sandboxUse(s)"
                    :key="i"
                    class="mc-use__part"
                    :class="{ 'mc-use__part--near': part.near }"
                    >{{ part.text }}</span
                  >
                </span>
              </div>
            </div>
          </template>
          <!-- 温机还不是宿主机，但已经在跑、在花钱：同一套列，阶段落在「阶段」列下。 -->
          <div v-for="w in warm" :key="w.id" class="mc-row mc-row--warm" role="row">
            <span class="mc-caret" />
            <span class="mc-host">
              <span class="mc-mono c-ink">{{ w.hostname ?? '—' }}</span>
              <span v-if="w.machine_id !== null" class="t-meta">#{{ w.machine_id }}</span>
              <span class="chip-neutral">{{ t('admin.machines.warmChip') }}</span>
            </span>
            <span class="mc-phase">
              <span
                class="status-dot"
                :class="w.error ? 'status-dot--danger' : w.state === 'ready' ? 'status-dot--ok' : 'status-dot--muted'"
              />
              {{ warmState(w.state) }}
            </span>
            <span class="mc-dim mc-spec">{{ spec(w) }}</span>
            <span class="mc-slots" />
            <span class="mc-dim mc-num mc-age" :title="stamp(w.created_at)">{{ since(w.created_at) }}</span>
            <span class="mc-note" :title="w.error ?? ''">{{
              w.error ?? (w.attempts ? t('admin.machines.attempts', { n: w.attempts }, w.attempts) : '')
            }}</span>
          </div>
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
      <p class="mc-foot">
        {{
          t('admin.machines.usage.foot', {
            check: duration(overview.config.check_s),
            disk: fmtPercent(overview.config.alert_used.disk, 0),
            inodes: fmtPercent(overview.config.alert_used.inodes, 0),
            memory: fmtPercent(overview.config.alert_used.memory, 0),
            near: fmtPercent(overview.config.near_cap, 0),
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
.mc-row--warm {
  cursor: default;
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
/* 宿主机那一行下面的用量：四根条排一行，窄屏一根一行。 */
.mc-usage {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px 24px;
  min-width: 840px;
  padding: 2px 12px 10px 40px;
  border-bottom: 1px solid var(--line);
}
.mc-usage__meter {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.mc-usage__read {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
.mc-usage__read--stale {
  color: var(--warn-ink);
}
.mc-use {
  display: flex;
  flex-wrap: wrap;
  grid-column: 2 / -1;
  gap: 2px 12px;
  font-size: 13px;
  color: var(--muted);
}
.mc-use__part--near {
  color: var(--warn-ink);
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
      '. note note'
      '. use use';
  }
  .mc-use {
    grid-area: use;
  }
  .mc-usage {
    grid-template-columns: minmax(0, 1fr);
    min-width: 0;
    padding-left: 40px;
  }
}
</style>
