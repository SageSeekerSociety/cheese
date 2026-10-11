<script setup lang="ts">
// 「现在的分布」里直接跑在某台电脑上的 agent：列出来，选一些换到别的环境——沙箱，
// 或者自己的另一台电脑。每条会话各换各的（#2320），同一段对话里别的队友不跟着搬；换之
// 前它先尽力推送一次，推没推上去都换。正在干活的跳过，不打断。
import type { Environment, EnvironmentDevice, SandboxSpec } from '../types/compute'
import type { DeviceSession } from '../types/deviceSessions'

import { computed, ref, watch } from 'vue'

import { listDeviceSessions, setSessionEnvironment } from '../api'
import { t } from '../i18n'
import { teammateName } from '../lib/agentNames'
import { environmentName } from '../lib/environment'
import { relTime } from '../lib/relTime'
import { topicTitle } from '../lib/topicState'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<{
  projectId: string
  device: { device_id: string; name: string }
  devices: EnvironmentDevice[]
  specs: SandboxSpec[]
}>()
// The distribution counts move once any session has switched. Told when the
// dialog closes: reloading under it could remove the row it hangs on.
const emit = defineEmits<{ changed: [] }>()

type Outcome = { state: 'done' | 'failed'; message: string }

const open = ref(false)
const loading = ref(false)
const loadError = ref('')
const sessions = ref<DeviceSession[]>([])
const hidden = ref(0)
const selected = ref<string[]>([])
const target = ref('')
const running = ref(false)
const outcomes = ref<Record<string, Outcome>>({})
const moved = ref(false)

// 换到哪：每种规格的沙箱，以及自己登记的另一台电脑（别人的电脑只有它的主人能指定）。
const choices = computed(() => {
  const options: { title: string; value: string; props: { disabled: boolean }; env: Environment }[] = props.specs.map(
    ({ spec }) => {
      const env: Environment = { kind: 'sandbox', spec }
      return { title: environmentName(env, [], props.specs), value: `sandbox:${spec}`, props: { disabled: false }, env }
    }
  )
  for (const d of props.devices) {
    if (d.device_id === props.device.device_id || !d.owned) continue
    const env: Environment = { kind: 'self_hosted', device_id: d.device_id }
    const name = environmentName(env, props.devices)
    options.push({
      title: d.online ? name : t('work.sessionMachine.unavailable', { name }),
      value: `device:${d.device_id}`,
      props: { disabled: !d.online },
      env,
    })
  }
  return options
})
const picked = computed<Environment | null>(
  () => choices.value.find((c) => c.value === target.value && !c.props.disabled)?.env ?? null
)
// A session already moved is no longer on this device; it cannot be picked again.
const selectable = computed(() => sessions.value.filter((s) => outcomes.value[s.id]?.state !== 'done'))
const allSelected = computed(
  () => selectable.value.length > 0 && selectable.value.every((s) => selected.value.includes(s.id))
)

const sessionRoom = (s: DeviceSession) => s.task_title ?? topicTitle({ title: s.topic_title })
// 会话所在的那段对话：房间自己的，或者某个任务的。
const conversationOf = (s: DeviceSession) => s.task_id ?? s.topic_id

function toggleAll() {
  selected.value = allSelected.value ? [] : selectable.value.map((s) => s.id)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const result = await listDeviceSessions(props.projectId, props.device.device_id)
    sessions.value = result.sessions
    hidden.value = result.hidden
  } catch (cause) {
    loadError.value = cause instanceof Error ? cause.message : t('work.bulkSwitch.loadFailed')
  } finally {
    loading.value = false
  }
}

async function switchOne(session: DeviceSession, env: Environment) {
  try {
    await setSessionEnvironment(conversationOf(session), session.id, env, { ifIdle: true })
    outcomes.value[session.id] = { state: 'done', message: t('work.bulkSwitch.done') }
    selected.value = selected.value.filter((id) => id !== session.id)
    moved.value = true
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : t('global.updateFailed')
    outcomes.value[session.id] = { state: 'failed', message }
  }
}

// One after another: every checkpoint runs on the same machine, the one being left.
async function run() {
  const env = picked.value
  if (!env || !selected.value.length) return
  running.value = true
  for (const session of sessions.value.filter((s) => selected.value.includes(s.id))) {
    await switchOne(session, env)
  }
  running.value = false
}

watch(open, (value) => {
  if (!value) {
    if (moved.value) emit('changed')
    return
  }
  moved.value = false
  selected.value = []
  outcomes.value = {}
  target.value = choices.value.find((choice) => !choice.props.disabled)?.value ?? ''
  void load()
})
</script>

<template>
  <button type="button" class="bs-open" @click="open = true">{{ t('work.bulkSwitch.open') }}</button>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.bulkSwitch.title', { name: device.name })"
    :primary-label="t('work.sessionMachine.confirm')"
    :primary-loading="running"
    :primary-disabled="running || !picked || !selected.length"
    :close-disabled="running"
    @primary="run"
  >
    <p v-if="loadError" role="alert" class="bs-error">{{ loadError }}</p>
    <v-progress-linear v-else-if="loading" indeterminate />
    <template v-else>
      <BaseEmptyState v-if="!sessions.length" size="inline" :title="t('work.bulkSwitch.empty')" />
      <template v-else>
        <v-checkbox
          :model-value="allSelected"
          :label="t('work.bulkSwitch.selectAll')"
          :disabled="running"
          density="compact"
          hide-details
          @update:model-value="toggleAll"
        />
        <ul class="bs-list">
          <li v-for="session in sessions" :key="session.id" class="bs-row">
            <v-checkbox
              v-model="selected"
              :value="session.id"
              :disabled="running || outcomes[session.id]?.state === 'done'"
              :aria-label="sessionRoom(session)"
              density="compact"
              hide-details
            />
            <div class="bs-body">
              <div class="bs-room">{{ sessionRoom(session) }}</div>
              <div class="bs-meta">
                <i18n-t keypath="work.bulkSwitch.meta" tag="span">
                  <template #agent
                    ><UserRef
                      :handle="session.agent_handle"
                      :name="teammateName(session.agent_name, session.agent_name_source)"
                  /></template>
                  <template #time>{{ relTime(session.last_active) }}</template>
                </i18n-t>
                <template v-if="session.working"> · {{ t('work.bulkSwitch.working') }}</template>
              </div>
              <p
                v-if="outcomes[session.id]"
                :role="outcomes[session.id].state === 'done' ? 'status' : 'alert'"
                :class="outcomes[session.id].state === 'done' ? 'bs-done' : 'bs-error'"
              >
                {{ outcomes[session.id].message }}
              </p>
            </div>
          </li>
        </ul>
      </template>
      <p v-if="hidden" class="c-muted">{{ t('work.bulkSwitch.hidden', { count: hidden }) }}</p>
      <v-select
        v-if="sessions.length"
        v-model="target"
        class="mt-4"
        autocomplete="off"
        :items="choices"
        :label="t('work.sessionMachine.machine')"
        :disabled="running"
      />
      <p v-if="sessions.length" role="note">{{ t('work.bulkSwitch.pushFirst') }}</p>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.bs-open {
  flex: none;
  margin-left: auto;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.bs-open:hover {
  color: var(--ink);
}
.bs-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.bs-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  border-top: 1px solid var(--line);
}
.bs-body {
  min-width: 0;
  padding-top: 8px;
}
.bs-room {
  color: var(--ink);
}
.bs-meta {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.bs-done {
  margin: 4px 0 0;
  color: var(--ok-ink);
}
.bs-error {
  margin: 4px 0 0;
  color: var(--danger-ink);
}
</style>
