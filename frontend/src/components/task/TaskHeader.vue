<script setup lang="ts">
// 任务页的页头，和房间页头是同一条线：同样的高度和底线，手机上同样填进顶栏那一格。
// 左边是「# 房间 / 任务名」和任务此刻的状态；右边是负责人和协作者（点开看谁在做、
// 在哪台电脑上做，负责人在这里增减协作者），以及「开始」和 ⋯（重命名归负责人和协作者，
// 转交、关闭只归负责人）。
import type { RoomTask, Topic, TopicMemberRow } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'
import { t } from '@/i18n'
import { phraseLabel } from '@/lib/board'
import { choiceName } from '@/lib/computeConfig'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'

const props = defineProps<{
  room: Topic
  task: RoomTask | null
  memberNames: Record<string, string>
  agentName: string
  people: TopicMemberRow[]
  machine: TopicComputeProfile | null
  machineError: boolean
  starting: boolean
  startError: string | null
  actionError: string | null
  connected?: boolean
  start: (reviewer: string | null) => Promise<void>
  close: (conclusion: string) => Promise<boolean>
  handOver: (owner: string) => Promise<boolean>
  rename: (title: string) => Promise<boolean>
  setCollaborators: (handles: string[]) => Promise<boolean>
  loadMachine: () => Promise<void>
  /** 右侧面板是不是开着——「概览」那颗开关读它。 */
  panelOpen?: boolean
}>()

const emit = defineEmits<{
  (e: 'open-room'): void
  (e: 'toggle-panel'): void
}>()

const { mdAndUp } = useDisplay()
const ME = myHandle()
const isOwner = computed(() => !!props.task && props.task.owner_handle === ME)
const isOpen = computed(() => props.task?.status === 'open')
const takesPart = computed(() => isOwner.value || (!!props.task && (props.task.contributor_handles ?? []).includes(ME)))
const ownerName = computed(() => {
  const handle = props.task?.owner_handle ?? ''
  return props.memberNames[handle] || handle
})
const taskAgentName = computed(() => {
  const handle = props.task?.agent_handle
  return (handle && props.memberNames[handle]) || props.agentName
})
const otherPeople = computed(() => props.people.filter((m) => m.member_handle !== ME))
const collaborators = computed(() => props.task?.contributor_handles ?? [])
const nameOf = (handle: string) => props.memberNames[handle] || handle
// 还能拉进来的人：名册上的人，除了负责人和已经在协作的。
const addable = computed(() =>
  props.people.filter(
    (m) => m.member_handle !== props.task?.owner_handle && !collaborators.value.includes(m.member_handle)
  )
)
const adding = ref('')
async function addCollaborator() {
  if (!adding.value) return
  if (await props.setCollaborators([...collaborators.value, adding.value])) adding.value = ''
}
function removeCollaborator(handle: string) {
  void props.setCollaborators(collaborators.value.filter((h) => h !== handle))
}

const detailsOpen = ref(false)
watch(detailsOpen, (open) => {
  if (open) void props.loadMachine()
})

const reviewer = ref('')

const closeOpen = ref(false)
const closing = ref(false)
const conclusion = ref('')
async function confirmClose() {
  closing.value = true
  try {
    if (await props.close(conclusion.value)) closeOpen.value = false
  } finally {
    closing.value = false
  }
}

const renameOpen = ref(false)
const renaming = ref(false)
const newTitle = ref('')
watch(renameOpen, (open) => {
  if (open) newTitle.value = props.task?.title ?? ''
})
async function confirmRename() {
  renaming.value = true
  try {
    if (await props.rename(newTitle.value)) renameOpen.value = false
  } finally {
    renaming.value = false
  }
}

const handOverOpen = ref(false)
const handingOver = ref(false)
const nextOwner = ref('')
async function confirmHandOver() {
  handingOver.value = true
  try {
    if (await props.handOver(nextOwner.value)) handOverOpen.value = false
  } finally {
    handingOver.value = false
  }
}
</script>

<template>
  <Teleport :key="String(mdAndUp)" to="#app-bar-slot" :disabled="mdAndUp" defer>
    <div class="task-header" :class="{ 'task-header--bar': !mdAndUp }">
      <div class="task-header__text">
        <button v-if="mdAndUp" type="button" class="task-header__room t-title" @click="emit('open-room')">
          # {{ topicTitle(room) }}
        </button>
        <span v-if="mdAndUp" class="task-header__sep t-title" aria-hidden="true">/</span>
        <span class="task-header__title t-title" :title="task ? taskTitle(task) : ''">{{
          task ? taskTitle(task) : ''
        }}</span>
        <span v-if="task" class="task-header__state" data-testid="task-phrase">{{
          phraseLabel(task.presentation.phrase)
        }}</span>
        <span v-if="connected === false" class="task-header__disconnected" role="status">{{
          t('work.room.header.disconnected')
        }}</span>
      </div>

      <v-menu v-if="task?.owner_handle" v-model="detailsOpen" location="bottom end" :close-on-content-click="false">
        <template #activator="{ props: menuProps }">
          <button
            type="button"
            class="task-header__owner"
            v-bind="menuProps"
            :title="t('work.task.details')"
            data-testid="task-details"
          >
            <UserAvatar :size="20" :name="ownerName" />
            <span class="task-header__owner-name">{{ t('work.task.ownerChip', { name: ownerName }) }}</span>
            <span v-if="collaborators.length" class="task-header__helpers" aria-hidden="true">
              <UserAvatar
                v-for="handle in collaborators.slice(0, 3)"
                :key="handle"
                :size="18"
                :name="nameOf(handle)"
                class="task-header__helper"
              />
            </span>
          </button>
        </template>
        <v-card class="task-details">
          <dl class="task-details__list t-meta">
            <div class="task-details__row">
              <dt>{{ t('work.task.owner') }}</dt>
              <dd><UserRef :handle="task.owner_handle" /></dd>
            </div>
            <div
              v-if="collaborators.length || (isOwner && isOpen)"
              class="task-details__row"
              data-testid="task-collaborators"
            >
              <dt :title="t('work.task.collaboratorsHint')">{{ t('work.task.collaborators') }}</dt>
              <dd class="task-details__people">
                <span v-for="handle in collaborators" :key="handle" class="task-details__person">
                  <UserRef :handle="handle" />
                  <button
                    v-if="isOpen && (isOwner || handle === ME)"
                    type="button"
                    class="task-details__retry"
                    @click="removeCollaborator(handle)"
                  >
                    {{ handle === ME ? t('work.task.leaveCollaboration') : t('work.task.removeCollaborator') }}
                  </button>
                </span>
                <span v-if="isOwner && isOpen && addable.length" class="task-details__person">
                  <select
                    v-model="adding"
                    class="task-notice__select t-meta"
                    :aria-label="t('work.task.addCollaborator')"
                  >
                    <option value="">{{ t('work.task.addCollaborator') }}</option>
                    <option v-for="m in addable" :key="m.member_handle" :value="m.member_handle">
                      {{ nameOf(m.member_handle) }}
                    </option>
                  </select>
                  <button v-if="adding" type="button" class="task-details__retry" @click="addCollaborator">
                    {{ t('work.task.addCollaborator') }}
                  </button>
                </span>
              </dd>
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
                  :topic-id="task.id"
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
        @click="start(reviewer || null)"
        >{{ t('work.task.start') }}</BaseButton
      >
      <PanelToggle v-if="mdAndUp" :open="!!panelOpen" @toggle="emit('toggle-panel')" />
      <v-menu v-if="task && takesPart" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="sm"
            class="tap-target"
            :title="t('work.task.more')"
            :aria-label="t('work.task.more')"
            data-testid="task-more"
          />
        </template>
        <v-list density="compact">
          <v-list-item :title="t('work.task.rename')" data-testid="task-rename" @click="renameOpen = true" />
          <template v-if="isOwner && isOpen">
            <v-list-item v-if="otherPeople.length" :title="t('work.task.handOver')" @click="handOverOpen = true" />
            <v-list-item :title="t('work.task.close')" @click="closeOpen = true" />
          </template>
        </v-list>
      </v-menu>
    </div>
  </Teleport>

  <p v-if="actionError" class="task-notice t-meta" role="alert">{{ actionError }}</p>
  <div v-if="startError" class="task-notice t-meta" role="alert">
    <span>{{ startError }}</span>
    <label class="task-notice__pick">
      <span>{{ t('work.task.reviewer') }}</span>
      <select v-model="reviewer" class="task-notice__select t-meta">
        <option value="">{{ t('work.task.reviewerNone') }}</option>
        <option v-for="m in people" :key="m.member_handle" :value="m.member_handle">
          {{ memberNames[m.member_handle] || m.member_handle }}
        </option>
      </select>
    </label>
  </div>

  <ConfirmDialog
    v-model="closeOpen"
    :title="t('work.task.closeTitle')"
    :confirm-label="t('work.task.close')"
    :loading="closing"
    @confirm="confirmClose"
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
    v-model="renameOpen"
    :title="t('work.task.renameTitle')"
    :confirm-label="t('work.task.rename')"
    :loading="renaming"
    :disabled="!newTitle.trim()"
    @confirm="confirmRename"
  >
    <input
      v-model="newTitle"
      type="text"
      maxlength="80"
      autocomplete="off"
      class="task-dialog__input t-body"
      :aria-label="t('work.task.renameLabel')"
      @keydown.enter.prevent="newTitle.trim() && confirmRename()"
    />
  </ConfirmDialog>
  <ConfirmDialog
    v-model="handOverOpen"
    :title="t('work.task.handOverTitle')"
    :confirm-label="t('work.task.handOver')"
    :loading="handingOver"
    :disabled="!nextOwner"
    @confirm="confirmHandOver"
  >
    <select v-model="nextOwner" class="task-dialog__input t-body" :aria-label="t('work.task.handOverTo')">
      <option value="" disabled>{{ t('work.task.handOverTo') }}</option>
      <option v-for="m in otherPeople" :key="m.member_handle" :value="m.member_handle">
        {{ memberNames[m.member_handle] || m.member_handle }}
      </option>
    </select>
  </ConfirmDialog>
</template>

<style scoped>
.task-header__helpers {
  display: inline-flex;
  margin-left: 2px;
}
.task-header__helper + .task-header__helper {
  margin-left: -6px;
}
.task-details__people {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.task-details__person {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.task-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  /* 和房间页头同一条基线：高度和底线读同一个 token（TopicHeader）。 */
  height: var(--app-page-header-height);
  padding: 0 12px;
  background: var(--surface);
  border-bottom: var(--app-page-header-rule);
}
.task-header--bar {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  padding: 0;
  background: none;
  border-bottom: 0;
}
.task-header__text {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.task-header__room {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 40%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: var(--muted);
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.task-header__room:hover {
  color: var(--ink);
}
.task-header__sep {
  flex: none;
  color: var(--faint);
}
.task-header__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 和房间页头的状态标同一个样子。 */
.task-header__state {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.task-header__disconnected {
  flex: 0 0 auto;
  color: var(--warn-ink);
  font-size: 12px;
}
.task-header__owner {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-header__owner:hover {
  background: var(--fill);
}
.task-header__owner-name {
  white-space: nowrap;
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
.task-notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 0;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.task-notice__pick {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.task-notice__select {
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
</style>
