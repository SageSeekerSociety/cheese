<script setup lang="ts">
import type { ComputeChoice, SessionDispatch, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { computed, ref, watch } from 'vue'

import {
  approveSessionWorkChoice,
  confirmSessionDispatch,
  getSessionDispatches,
  getSessionWorkLeases,
  setSessionWorkChoice,
} from '../api'
import { choiceDetail, choiceKey, compactChoices } from '../lib/computeConfig'
import { currentUserName } from '../services/account'

import ComputeChoiceForm from './ComputeChoiceForm.vue'

const props = defineProps<{ topicId: string; profile: TopicComputeProfile }>()
const open = ref(false)
const sessions = ref<SessionWorkLease[]>([])
const selectedId = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const dispatches = ref<SessionDispatch[]>([])
const canConfirm = ref(false)
const acknowledgeUnreachable = ref(false)
const notes = ref<Record<string, string>>({})
const selected = computed(() => sessions.value.find((session) => session.id === selectedId.value))
const choices = computed(() =>
  compactChoices(props.profile.project_default, props.profile.favorites, selected.value?.choice)
)
const unresolved = computed(() =>
  dispatches.value.filter((dispatch) => !dispatch.confirmed_at && (!dispatch.outcome || dispatch.outcome === 'unknown'))
)

async function load() {
  busy.value = true
  error.value = ''
  try {
    const result = await getSessionWorkLeases(props.topicId)
    sessions.value = result.sessions
    if (!sessions.value.some((session) => session.id === selectedId.value))
      selectedId.value = sessions.value[0]?.id ?? ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载会话失败'
  } finally {
    busy.value = false
  }
}

async function act(operation: () => Promise<unknown>, success: string) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await operation()
    await load()
    notice.value = success
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function pick(choice: ComputeChoice) {
  const session = selected.value
  if (!session) return
  await act(() => setSessionWorkChoice(props.topicId, session.id, choice), '已提交选择，请查看当前配置和待批准变更。')
}

async function approve() {
  const session = selected.value
  if (!session?.pending) return
  const proposalId = session.pending.id
  await act(
    () => approveSessionWorkChoice(props.topicId, session.id, proposalId, acknowledgeUnreachable.value),
    '变更已批准，下次执行操作时准备新机器。'
  )
}

async function inspect() {
  const session = selected.value
  if (!session) return
  busy.value = true
  error.value = ''
  try {
    const result = await getSessionDispatches(props.topicId, session.id)
    dispatches.value = result.dispatches
    canConfirm.value = result.can_confirm
    if (!unresolved.value.length) notice.value = '没有需要核实的未知操作。'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载操作记录失败'
  } finally {
    busy.value = false
  }
}

async function confirm(dispatch: SessionDispatch) {
  const session = selected.value
  const note = notes.value[dispatch.id]?.trim()
  if (!session || !note) return
  await act(
    () => confirmSessionDispatch(props.topicId, session.id, dispatch.id, note),
    '核实记录已保存，原操作不会自动重试。'
  )
  if (!error.value) await inspect()
}

watch(open, (value) => {
  if (value) void load()
})
watch(selectedId, () => {
  canConfirm.value = false
  dispatches.value = []
  notes.value = {}
  notice.value = ''
  error.value = ''
})
watch(
  () => selected.value?.pending?.id,
  () => {
    acknowledgeUnreachable.value = false
  }
)
watch(
  () => props.topicId,
  () => {
    open.value = false
    sessions.value = []
    selectedId.value = ''
    dispatches.value = []
  }
)
</script>

<template>
  <v-dialog v-model="open" max-width="620">
    <template #activator="{ props: activator }">
      <v-btn v-bind="activator" variant="text" size="small">会话执行机器</v-btn>
    </template>
    <v-card title="会话执行机器">
      <v-card-text>
        <p class="mb-4">为具体队友会话选择执行机器。会话继续保留，文件不会自动搬到新机器。</p>
        <v-select
          v-model="selectedId"
          autocomplete="off"
          :items="
            sessions.map((session) => ({
              title: `${session.agent_handle} · ${session.id.slice(0, 8)}`,
              value: session.id,
            }))
          "
          label="队友会话"
          :disabled="busy"
        />
        <p v-if="!busy && !sessions.length && !error">还没有队友会话。开始对话后可在这里选择执行机器。</p>
        <template v-if="selected">
          <p>当前配置：{{ selected.choice.name }}</p>
          <p class="mb-4">
            {{ selected.lease ? `已分配机器：${selected.lease.device_id}` : '首次执行操作时准备机器' }}
          </p>
          <section v-if="selected.pending" class="sw-pending" aria-label="待批准变更">
            <p>待批准：{{ selected.pending.choice.name }}</p>
            <p>批准人：{{ selected.pending.approver }}</p>
            <p>批准后，下次执行操作使用新配置。旧云机器会保留，并继续占用团队云机器额度。</p>
            <template v-if="selected.pending.approver === currentUserName">
              <template v-if="selected.lease?.online === false">
                <p>
                  旧机器离线，平台无法确认后台工作是否结束。请先核实未完成的工作；批准后仍保留旧机器，不会自动重放操作。
                </p>
                <v-checkbox
                  v-model="acknowledgeUnreachable"
                  label="我已核实旧机器上的未完成工作，确认换机"
                  :disabled="busy"
                />
              </template>
              <v-btn :disabled="busy || (selected.lease?.online === false && !acknowledgeUnreachable)" @click="approve"
                >批准变更</v-btn
              >
            </template>
            <p v-else>等待批准人处理。</p>
          </section>
          <div class="sw-choices">
            <v-btn
              v-for="choice in choices"
              :key="choiceKey(choice)"
              variant="outlined"
              :disabled="busy"
              @click="pick(choice)"
              >{{ choice.name }}</v-btn
            >
          </div>
          <p class="text-caption">{{ choiceDetail(selected.choice) }}</p>
          <ComputeChoiceForm
            :key="selected.id"
            :devices="profile.devices"
            :cloud-available="profile.profiles.some((p) => p.id === 'cloud' && p.available)"
            :busy="busy"
            @select="pick"
          />
          <v-btn variant="text" :disabled="busy" @click="inspect">查看待核实操作</v-btn>
          <section
            v-for="dispatch in unresolved"
            :key="dispatch.id"
            class="sw-pending"
            :aria-label="`待核实操作 ${dispatch.tool}`"
          >
            <p>{{ dispatch.tool }} · {{ dispatch.dispatched_at || '发送时间未知' }}</p>
            <p>结果未知。项目管理员需要先检查旧机器上的实际结果，再记录核实情况。保存核实记录不会重新执行这个操作。</p>
            <template v-if="canConfirm">
              <v-textarea v-model="notes[dispatch.id]" autocomplete="off" label="核实情况" :disabled="busy" rows="2" />
              <v-btn :disabled="busy || !notes[dispatch.id]?.trim()" @click="confirm(dispatch)">已核实，保存记录</v-btn>
            </template>
          </section>
        </template>
        <p v-if="notice" role="status">{{ notice }}</p>
        <p v-if="error" role="alert" class="sw-error">{{ error }}</p>
      </v-card-text>
      <v-card-actions>
        <v-btn :disabled="busy" @click="load">刷新</v-btn>
        <v-spacer />
        <v-btn @click="open = false">关闭</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.sw-pending {
  padding: 12px;
  margin: 12px 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.sw-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}
.sw-error {
  color: var(--danger-ink);
}
</style>
