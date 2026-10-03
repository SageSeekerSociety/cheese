<script setup lang="ts">
// 「现在的分布」里一台自有设备上的 agent：列出来，选一些换到另一台工作电脑。一个话题
// 一个容器（2026-09-28，推翻结论 60）：换的是它所在的整个房间，走和成员名册同一条
// 更换（先推送，失败就不换并说明原因），同房间的队友一起搬；房间正在干活的跳过，不打断。
import type { ComputeChoice, TopicComputeDevice } from '../cx_types'
import type { DeviceSession } from '../types/deviceSessions'

import { computed, ref, watch } from 'vue'

import { ApiError, listDeviceSessions, setTopicComputeChoice } from '../api'
import { t } from '../i18n'
import { teammateName } from '../lib/agentNames'
import { choiceKey, choiceName, compactChoices } from '../lib/computeConfig'
import { relTime } from '../lib/relTime'
import { topicTitle } from '../lib/topicState'

import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<{
  projectId: string
  device: { device_id: string; name: string }
  devices: TopicComputeDevice[]
  cloudAvailable: boolean
  projectDefault: ComputeChoice
}>()
// The distribution counts move once any session has switched. Told when the
// dialog closes: reloading under it could remove the row it hangs on.
const emit = defineEmits<{ changed: [] }>()

type Outcome = { state: 'done' | 'failed' | 'unreachable'; message: string }

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

const choices = computed(() => {
  const cloud: ComputeChoice = {
    name: null,
    profile: 'cloud',
    device_id: null,
    cores: null,
    memory_mb: null,
    disk_gb: null,
  }
  const devices = props.devices
    .filter((device) => device.device_id !== props.device.device_id)
    .map((device): ComputeChoice => ({ ...cloud, name: device.name, profile: 'device', device_id: device.device_id }))
  const leaving = (choice: ComputeChoice) => choice.profile === 'device' && choice.device_id === props.device.device_id
  return compactChoices(props.projectDefault, cloud, ...devices)
    .filter((choice) => !leaving(choice))
    .map((choice) => {
      const available =
        choice.profile === 'cloud'
          ? props.cloudAvailable
          : props.devices.some(
              (device) =>
                device.device_id !== props.device.device_id &&
                (!choice.device_id || device.device_id === choice.device_id) &&
                device.online
            )
      return {
        title: available ? choiceName(choice) : t('work.sessionMachine.unavailable', { name: choiceName(choice) }),
        value: choiceKey(choice),
        props: { disabled: !available },
        choice,
      }
    })
})
const picked = computed(
  () => choices.value.find((choice) => choice.value === target.value && !choice.props.disabled)?.choice
)
// A session already moved is no longer on this device; it cannot be picked again.
const selectable = computed(() => sessions.value.filter((s) => outcomes.value[s.id]?.state !== 'done'))
const allSelected = computed(
  () => selectable.value.length > 0 && selectable.value.every((s) => selected.value.includes(s.id))
)

const sessionRoom = (s: DeviceSession) => topicTitle({ title: s.topic_title, title_source: s.topic_title_source })

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

// 换的是房间：答案落在这个房间在这台设备上的每一行上。
function settle(topicId: string, outcome: Outcome) {
  const room = sessions.value.filter((s) => s.topic_id === topicId).map((s) => s.id)
  for (const id of room) outcomes.value[id] = outcome
  if (outcome.state === 'done') selected.value = selected.value.filter((id) => !room.includes(id))
}

async function switchOne(session: DeviceSession, choice: ComputeChoice, abandonUnpushed = false) {
  try {
    await setTopicComputeChoice(session.topic_id, choice, { ifIdle: true, abandonUnpushed })
    settle(session.topic_id, { state: 'done', message: t('work.bulkSwitch.done') })
    moved.value = true
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : t('global.updateFailed')
    const unreachable = cause instanceof ApiError && cause.code === 'WorkComputerUnreachable'
    settle(session.topic_id, { state: unreachable ? 'unreachable' : 'failed', message })
  }
}

// One after another: every push runs on the same machine, the one being left.
async function run() {
  const choice = picked.value
  if (!choice || !selected.value.length) return
  running.value = true
  // 一个房间只换一次：同房间的几行选了几行，都是同一次更换。
  const rooms = new Set<string>()
  for (const session of sessions.value.filter((s) => selected.value.includes(s.id))) {
    if (rooms.has(session.topic_id)) continue
    rooms.add(session.topic_id)
    await switchOne(session, choice)
  }
  running.value = false
}

// The one override, per session, after that session's machine could not be
// reached: the person decides for this agent that its unpushed work stays behind.
async function abandon(session: DeviceSession) {
  const choice = picked.value
  if (!choice) return
  running.value = true
  await switchOne(session, choice, true)
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
  <v-dialog v-model="open" max-width="560" scrollable>
    <template #activator="{ props: activator }">
      <button v-bind="activator" type="button" class="bs-open">{{ t('work.bulkSwitch.open') }}</button>
    </template>
    <v-card :title="t('work.bulkSwitch.title', { name: device.name })">
      <v-card-text>
        <p v-if="loadError" role="alert" class="bs-error">{{ loadError }}</p>
        <v-progress-linear v-else-if="loading" indeterminate />
        <template v-else>
          <p v-if="!sessions.length" class="c-muted">{{ t('work.bulkSwitch.empty') }}</p>
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
                  <template v-if="outcomes[session.id]?.state === 'unreachable'">
                    <p class="bs-error">{{ t('work.sessionMachine.abandonWarning') }}</p>
                    <v-btn size="small" variant="text" :disabled="running" @click="abandon(session)">{{
                      t('work.sessionMachine.abandon')
                    }}</v-btn>
                  </template>
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
      </v-card-text>
      <v-card-actions>
        <v-btn variant="text" :disabled="running" @click="open = false">{{ t('global.cancel') }}</v-btn>
        <v-spacer />
        <v-btn
          color="primary"
          variant="tonal"
          :disabled="running || !picked || !selected.length"
          :loading="running"
          @click="run"
          >{{ t('work.sessionMachine.confirm') }}</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
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
