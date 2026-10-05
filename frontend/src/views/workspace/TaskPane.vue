<script setup lang="ts">
// 任务页：左边是任务自己的对话，右边是它的实况文档。
//
// 任务挂在房间下，打开任务时房间已经打开（名册、外框都借它的），所以这一块由房间页
// 在地址里带着 taskId 时画出来。只有负责人能在对话里说话、能点「开始」；别人看得
// 到全部，输入框的位置换成回到房间的入口。
import type { Block, ProjectMemberRow, RoomTask, Topic, TopicMemberRow, WsServerFrame } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { computed, ref, watch } from 'vue'

import { ApiError, editMessage, getTopicComputeProfile } from '@/api'
import {
  closeRoomTask,
  compareDocumentVersions,
  getRoomTask,
  sayOnRoomTask,
  startRoomTask,
  updateRoomTask,
} from '@/api/tasks'
import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import PanelChanges from '@/components/panels/PanelChanges.vue'
import PanelDoc from '@/components/panels/PanelDoc.vue'
import PanelSite from '@/components/panels/PanelSite.vue'
import { useRoomSocket } from '@/components/room/composables/useRoomSocket'
import TaskConversation from '@/components/task/TaskConversation.vue'
import TaskDocCompare from '@/components/task/TaskDocCompare.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'
import { t } from '@/i18n'
import { phraseLabel } from '@/lib/board'
import { choiceName } from '@/lib/computeConfig'
import { relTime } from '@/lib/relTime'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'

const props = defineProps<{
  room: Topic
  taskId: string
  members: ProjectMemberRow[]
  roomMembers: TopicMemberRow[]
  memberNames: Record<string, string>
  agentName: string
  agentHandle: string | null
  topicList: Topic[]
  focusBlock?: string | null
  phone?: boolean
}>()

const emit = defineEmits<{
  (e: 'open-room'): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()

const ME = myHandle()

type TaskWithBlocks = RoomTask & { blocks: Block[] }
const task = ref<TaskWithBlocks | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)

async function load(silent = false) {
  const room = props.room.id
  const id = props.taskId
  if (!silent) loading.value = true
  loadError.value = null
  try {
    let payload: TaskWithBlocks
    try {
      payload = await getRoomTask(room, id, { limit: 300, through: props.focusBlock ?? undefined })
    } catch (e) {
      if (!(props.focusBlock && e instanceof ApiError && e.status === 404)) throw e
      payload = await getRoomTask(room, id, { limit: 300 })
    }
    if (props.room.id !== room || props.taskId !== id) return
    task.value = payload
  } catch (e) {
    if (props.room.id !== room || props.taskId !== id) return
    loadError.value = e instanceof ApiError && e.status === 404 ? t('work.task.notFound') : t('work.task.loadFailed')
  } finally {
    if (props.room.id === room && props.taskId === id) loading.value = false
  }
}

// 房间页按 taskId 给这一块换新实例，所以这里只管第一次读。
void load()

const isOwner = computed(() => !!task.value && task.value.owner_handle === ME)
const isOpen = computed(() => task.value?.status === 'open')
const blocked = computed<'not-owner' | 'closed' | null>(() =>
  !isOpen.value ? 'closed' : isOwner.value ? null : 'not-owner'
)

// ---- 文档跟着 AI 队友的动作重读 ----
const docTick = ref(0)

function mergeBlock(updated: Block) {
  const blocks = task.value?.blocks
  if (!blocks) return
  const at = blocks.findIndex((b) => b.id === updated.id)
  if (at >= 0) {
    blocks.splice(at, 1, updated)
    return
  }
  const next = blocks.findIndex((b) => b.created_at > updated.created_at)
  if (next < 0) blocks.push(updated)
  else blocks.splice(next, 0, updated)
}

function removeBlock(id: string) {
  const blocks = task.value?.blocks
  if (!blocks) return
  const at = blocks.findIndex((b) => b.id === id)
  if (at >= 0) blocks.splice(at, 1)
}

const socketError = ref<string | null>(null)
const socket = useRoomSocket({
  topicId: () => props.taskId,
  onFrame(frame: WsServerFrame) {
    if (frame.type === 'assistant_block' || frame.type === 'event_block' || frame.type === 'user_block') {
      mergeBlock(frame.block)
      siteRef.value?.receive(frame.block)
    } else if (frame.type === 'block_updated') {
      mergeBlock(frame.block)
      siteRef.value?.receive(frame.block)
    } else if (frame.type === 'retract_block') {
      removeBlock(frame.block_id)
    } else if (frame.type === 'done' || frame.type === 'turn_finished') {
      docTick.value += 1
      void load(true)
    } else if (frame.type === 'error' && socket.isConnectRefusal(frame.code)) {
      socket.connectRefused.value = true
    }
  },
  onOpen: () => {},
  reconnect(id) {
    void load(true)
    socket.open(id)
  },
  errorMsg: socketError,
})
watch(
  () => props.taskId,
  (id) => {
    socket.connectRefused.value = false
    socket.open(id)
  },
  { immediate: true }
)

// ---- 说话 ----
const draft = ref('')
const sending = ref(false)
const sendError = ref<string | null>(null)
async function send(text: string) {
  if (!task.value || sending.value) return
  sending.value = true
  sendError.value = null
  try {
    mergeBlock(await sayOnRoomTask(props.room.id, task.value.id, text))
    draft.value = ''
  } catch (e) {
    sendError.value = e instanceof ApiError && e.message ? e.message : t('work.task.sendFailed')
  } finally {
    sending.value = false
  }
}

const editSaving = ref(false)
const editError = ref<string | null>(null)
async function saveEdit(block: Block, text: string) {
  editSaving.value = true
  editError.value = null
  try {
    mergeBlock(await editMessage(block.id, text))
  } catch {
    editError.value = t('work.room.message.saveFailed')
  } finally {
    editSaving.value = false
  }
}

// ---- 开始 ----
// 项目没有默认审阅人时，开始会被拒，负责人在这里指定一位再开始。
const starting = ref(false)
const startError = ref<string | null>(null)
const reviewer = ref<string>('')
const people = computed(() => props.roomMembers.filter((m) => !m.agent))
async function start() {
  if (!task.value || starting.value) return
  starting.value = true
  startError.value = null
  try {
    const started = await startRoomTask(props.room.id, task.value.id, reviewer.value || null)
    task.value = { ...task.value, ...started }
  } catch (e) {
    startError.value = e instanceof ApiError && e.message ? e.message : t('work.task.startFailed')
  } finally {
    starting.value = false
  }
}

// ---- 负责人：转交、关闭 ----
const actionError = ref<string | null>(null)
const closing = ref(false)
const closeOpen = ref(false)
const conclusion = ref('')
async function closeTask() {
  if (!task.value || closing.value) return
  closing.value = true
  actionError.value = null
  try {
    const closed = await closeRoomTask(props.room.id, task.value.id, conclusion.value.trim() || undefined)
    task.value = { ...task.value, ...closed }
    closeOpen.value = false
  } catch (e) {
    actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
  } finally {
    closing.value = false
  }
}
const handOverOpen = ref(false)
const handingOver = ref(false)
const nextOwner = ref('')
const otherPeople = computed(() => people.value.filter((m) => m.member_handle !== ME))
async function handOver() {
  if (!task.value || !nextOwner.value || handingOver.value) return
  handingOver.value = true
  actionError.value = null
  try {
    const moved = await updateRoomTask(props.room.id, task.value.id, { owner_handle: nextOwner.value })
    task.value = { ...task.value, ...moved }
    handOverOpen.value = false
  } catch (e) {
    actionError.value = e instanceof ApiError && e.message ? e.message : t('work.task.actionFailed')
  } finally {
    handingOver.value = false
  }
}

// ---- 任务信息：负责人、AI 队友、工作电脑 ----
// 收在负责人那一格里，页头不再多一个按钮。打开时才读工作电脑。
const detailsOpen = ref(false)
const machine = ref<TopicComputeProfile | null>(null)
const machineError = ref(false)
async function loadMachine() {
  if (!task.value) return
  machineError.value = false
  try {
    machine.value = await getTopicComputeProfile(props.room.id, task.value.id)
  } catch {
    machineError.value = true
  }
}
watch(detailsOpen, (open) => {
  if (open) void loadMachine()
})
const ownerName = computed(() => {
  const handle = task.value?.owner_handle ?? ''
  return props.memberNames[handle] || handle
})
const taskAgentName = computed(() => {
  const handle = task.value?.agent_handle
  return (handle && props.memberNames[handle]) || props.agentName
})

// ---- 与开始时相比 ----
const comparing = ref(false)
const comparison = ref<{ before: string; after: string } | null>(null)
const compareError = ref<string | null>(null)
async function toggleCompare() {
  if (comparing.value) {
    comparing.value = false
    return
  }
  const current = task.value
  if (!current?.document_id || current.started_doc_version == null) return
  comparing.value = true
  comparison.value = null
  compareError.value = null
  try {
    const got = await compareDocumentVersions(current.document_id, current.started_doc_version)
    comparison.value = { before: got.before.content, after: got.after.content }
  } catch {
    compareError.value = t('work.task.compareFailed')
  }
}

// 右边三格：总览（实况文档）、现场和改动。手机上一屏放不下两栏，对话也是一格。
const sideTab = ref<'overview' | 'site' | 'changes'>('overview')
// 现场第一次打开时才挂上去，之后切走也留着，socket 上来的行接着往里收。
const siteRef = ref<InstanceType<typeof PanelSite> | null>(null)
const siteMounted = ref(false)
watch(sideTab, (tab) => {
  if (tab === 'site') siteMounted.value = true
})
const phoneTab = ref<'chat' | 'doc'>('chat')
function review() {
  sideTab.value = 'changes'
  phoneTab.value = 'doc'
}
</script>

<template>
  <div class="task-pane">
    <header class="task-head">
      <button type="button" class="task-head__room t-meta" @click="emit('open-room')"># {{ topicTitle(room) }}</button>
      <span class="task-head__sep t-meta" aria-hidden="true">/</span>
      <h1 class="task-head__title t-body">{{ task ? taskTitle(task) : '' }}</h1>
      <span v-if="task" class="task-head__phrase t-meta" data-testid="task-phrase">{{
        phraseLabel(task.presentation.phrase)
      }}</span>
      <span class="task-head__spacer" />
      <v-menu v-if="task?.owner_handle" v-model="detailsOpen" location="bottom end" :close-on-content-click="false">
        <template #activator="{ props: menuProps }">
          <button
            type="button"
            class="task-head__owner t-meta"
            v-bind="menuProps"
            :title="t('work.task.details')"
            data-testid="task-details"
          >
            {{ t('work.task.owner') }} {{ ownerName }}
          </button>
        </template>
        <v-card class="task-details">
          <dl class="task-details__list t-meta">
            <div class="task-details__row">
              <dt>{{ t('work.task.owner') }}</dt>
              <dd><UserRef :handle="task.owner_handle" /></dd>
            </div>
            <div class="task-details__row">
              <dt>{{ t('work.task.agent') }}</dt>
              <dd>{{ taskAgentName }}</dd>
            </div>
            <div class="task-details__row" data-testid="task-machine">
              <dt>{{ t('work.task.machine') }}</dt>
              <dd v-if="machine" class="task-details__machine">
                <span>{{ choiceName(machine.choice) }}</span>
                <span v-if="machine.follows_room" class="task-details__tag">{{ t('work.task.followsRoom') }}</span>
                <TopicComputePicker
                  v-if="isOwner && isOpen"
                  :topic-id="room.id"
                  :task-id="task.id"
                  :profile="machine"
                  @changed="loadMachine"
                />
              </dd>
              <dd v-else-if="machineError">
                {{ t('work.roomMachine.loadFailed') }}
                <button type="button" class="task-details__retry" @click="loadMachine">
                  {{ t('work.roomMachine.retry') }}
                </button>
              </dd>
              <dd v-else><v-progress-circular indeterminate size="14" width="2" /></dd>
            </div>
          </dl>
        </v-card>
      </v-menu>
      <BaseButton
        v-if="task && isOwner && isOpen && !task.started_at"
        kind="primary"
        size="sm"
        :loading="starting"
        data-testid="task-start"
        @click="start"
        >{{ t('work.task.start') }}</BaseButton
      >
      <v-menu v-if="task && isOwner && isOpen" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            size="sm"
            icon="mdi-dots-horizontal"
            :title="t('work.task.more')"
            :aria-label="t('work.task.more')"
            data-testid="task-more"
          />
        </template>
        <v-list density="compact">
          <v-list-item v-if="otherPeople.length" :title="t('work.task.handOver')" @click="handOverOpen = true" />
          <v-list-item :title="t('work.task.close')" @click="closeOpen = true" />
        </v-list>
      </v-menu>
    </header>
    <p v-if="actionError" class="task-start-error t-meta" role="alert">{{ actionError }}</p>

    <ConfirmDialog
      v-model="closeOpen"
      :title="t('work.task.closeTitle')"
      :confirm-label="t('work.task.close')"
      :loading="closing"
      @confirm="closeTask"
    >
      <p class="t-body mb-2">{{ t('work.task.closeBody') }}</p>
      <textarea
        v-model="conclusion"
        rows="3"
        autocomplete="off"
        class="task-dialog__input t-body"
        :aria-label="t('work.task.conclusion')"
        :placeholder="t('work.task.conclusion')"
      />
    </ConfirmDialog>
    <ConfirmDialog
      v-model="handOverOpen"
      :title="t('work.task.handOverTitle')"
      :confirm-label="t('work.task.handOver')"
      :loading="handingOver"
      :disabled="!nextOwner"
      @confirm="handOver"
    >
      <select v-model="nextOwner" class="task-dialog__input t-body" :aria-label="t('work.task.handOverTo')">
        <option value="" disabled>{{ t('work.task.handOverTo') }}</option>
        <option v-for="m in otherPeople" :key="m.member_handle" :value="m.member_handle">
          {{ memberNames[m.member_handle] || m.member_handle }}
        </option>
      </select>
    </ConfirmDialog>

    <div v-if="startError" class="task-start-error t-meta" role="alert">
      <span>{{ startError }}</span>
      <label class="task-start-error__pick">
        <span>{{ t('work.task.reviewer') }}</span>
        <select v-model="reviewer" class="task-start-error__select t-meta">
          <option value="">{{ t('work.task.reviewerNone') }}</option>
          <option v-for="m in people" :key="m.member_handle" :value="m.member_handle">
            {{ memberNames[m.member_handle] || m.member_handle }}
          </option>
        </select>
      </label>
    </div>

    <div v-if="!task" class="task-pane__state t-body">
      <v-progress-circular v-if="loading" indeterminate color="primary" size="24" />
      <template v-else>
        <span class="c-muted">{{ loadError ?? t('work.task.notFound') }}</span>
        <BaseButton v-if="loadError" kind="secondary" size="sm" @click="load()">{{ t('work.task.retry') }}</BaseButton>
      </template>
    </div>

    <template v-else>
      <div v-if="phone" class="task-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          class="task-tabs__tab t-meta"
          :aria-selected="phoneTab === 'chat'"
          @click="phoneTab = 'chat'"
        >
          {{ t('work.task.tabChat') }}
        </button>
        <button
          type="button"
          role="tab"
          class="task-tabs__tab t-meta"
          :aria-selected="phoneTab === 'doc'"
          @click="phoneTab = 'doc'"
        >
          {{ t('work.task.tabOverview') }}
        </button>
      </div>
      <div class="task-body">
        <TaskConversation
          v-show="!phone || phoneTab === 'chat'"
          v-model:draft="draft"
          class="task-body__chat"
          :blocks="task.blocks"
          :blocked="blocked"
          :room-title="topicTitle(room)"
          :agent-name="agentName"
          :member-names="memberNames"
          :sending="sending"
          :send-error="sendError"
          :edit-saving="editSaving"
          :edit-error="editError"
          :focus-block="focusBlock ?? null"
          @send="send"
          @save-edit="saveEdit"
          @open-room="emit('open-room')"
        />
        <aside v-show="!phone || phoneTab === 'doc'" class="task-body__side">
          <div class="task-side-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              class="task-tabs__tab t-meta"
              :aria-selected="sideTab === 'overview'"
              @click="sideTab = 'overview'"
            >
              {{ t('work.task.tabOverview') }}
            </button>
            <button
              type="button"
              role="tab"
              class="task-tabs__tab t-meta"
              :aria-selected="sideTab === 'site'"
              data-testid="task-tab-site"
              @click="sideTab = 'site'"
            >
              {{ t('work.task.tabSite') }}
            </button>
            <button
              type="button"
              role="tab"
              class="task-tabs__tab t-meta"
              :aria-selected="sideTab === 'changes'"
              @click="sideTab = 'changes'"
            >
              {{ t('work.task.tabChanges') }}
            </button>
          </div>
          <TopicAcceptCard :topic-id="room.id" :task-id="task.id" topic-status="active" @review="review" />
          <PanelSite
            v-if="siteMounted"
            v-show="sideTab === 'site'"
            ref="siteRef"
            class="task-body__doc"
            :topic="room"
            :task-id="task.id"
            :active="sideTab === 'site'"
            :refresh-tick="docTick"
            :member-names="memberNames"
            :agent-name="agentName"
            @open-topic="emit('open-topic', $event)"
            @mention-click="emit('mention-click', $event)"
          />
          <PanelChanges
            v-if="sideTab === 'changes'"
            class="task-body__doc"
            :topic-id="room.id"
            :task-id="task.id"
            :read-only="!isOwner || !isOpen"
            :project-id="room.project_id"
            :active="sideTab === 'changes'"
            :refresh-tick="docTick"
          />
          <template v-else-if="sideTab === 'overview'">
            <div v-if="task.started_at" class="task-started t-meta">
              <span>{{
                t('work.task.startedLine', {
                  who: memberNames[task.started_by ?? ''] || task.started_by || '',
                  when: relTime(task.started_at),
                })
              }}</span>
              <template v-if="task.document_id && task.started_doc_version != null">
                <span aria-hidden="true">·</span>
                <button type="button" class="task-started__compare" data-testid="task-compare" @click="toggleCompare">
                  {{ comparing ? t('work.task.compareBack') : t('work.task.compare') }}
                </button>
              </template>
            </div>
            <div v-if="task.conclusion" class="task-conclusion">
              <div class="t-meta c-faint">{{ t('work.task.conclusion') }}</div>
              <p class="t-body task-conclusion__text">{{ task.conclusion }}</p>
            </div>
            <template v-if="comparing">
              <p v-if="compareError" class="t-meta task-side__error" role="alert">{{ compareError }}</p>
              <TaskDocCompare v-else-if="comparison" :before="comparison.before" :after="comparison.after" />
              <div v-else class="task-pane__state">
                <v-progress-circular indeterminate color="primary" size="20" />
              </div>
            </template>
            <PanelDoc
              v-else
              class="task-body__doc"
              :topic="room"
              :task-id="task.id"
              :activity-tick="docTick"
              :topic-list="topicList"
              :agent-name="agentName"
              :agent-handle="agentHandle"
              :members="members"
              @open-topic="emit('open-topic', $event)"
              @mention-click="emit('mention-click', $event)"
            />
          </template>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.task-pane {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.task-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 16px;
  border-bottom: 1px solid var(--line);
}
.task-head__room {
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
}
.task-head__room:hover {
  color: var(--ink);
}
.task-head__sep {
  color: var(--faint);
}
.task-head__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-head__phrase {
  flex: none;
  color: var(--muted);
}
.task-head__spacer {
  flex: 1 1 auto;
}
.task-head__owner {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-head__owner:hover {
  background: var(--fill);
  color: var(--ink);
}
.task-details {
  width: 320px;
  max-width: calc(100vw - 32px);
  padding: 8px 12px;
}
.task-details__list {
  margin: 0;
}
.task-details__row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 4px 0;
}
.task-details__row dt {
  flex: none;
  width: 72px;
  color: var(--faint);
}
.task-details__row dd {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--text);
}
.task-details__machine {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.task-details__tag {
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.task-details__retry {
  border: 0;
  background: transparent;
  color: var(--muted);
  text-decoration: underline;
  cursor: pointer;
}
.task-start-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.task-start-error__pick {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.task-start-error__select {
  padding: 2px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
}
.task-dialog__input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--ink);
  resize: vertical;
}
.task-pane__state {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
}
.task-tabs,
.task-side-tabs {
  display: flex;
  gap: 4px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--line);
}
.task-tabs__tab {
  padding: 4px 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.task-tabs__tab[aria-selected='true'] {
  background: var(--fill);
  color: var(--ink);
}
.task-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.task-body__chat {
  flex: 1 1 0;
  min-width: 0;
  border-right: 1px solid var(--line);
}
.task-body__side {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}
.task-body__doc {
  flex: 1 1 auto;
  min-height: 0;
}
.task-started {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
}
.task-started__compare {
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.task-started__compare:hover {
  color: var(--ink);
  text-decoration: underline;
}
.task-conclusion {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.task-conclusion__text {
  margin: 4px 0 0;
  color: var(--ink);
  white-space: pre-wrap;
}
.task-side__error {
  padding: 16px;
  color: var(--danger-ink);
}
@media (max-width: 959px) {
  .task-body__chat {
    border-right: 0;
  }
}
</style>
