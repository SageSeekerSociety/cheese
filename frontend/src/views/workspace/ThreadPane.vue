<script setup lang="ts">
// 一条支线：页头一行，写它挂着的那条消息说的事，右边是「转为任务」——这段讨论原地变成
// 任务，还是这段对话、还是这位 AI 队友。转成以后页头写任务名、状态和负责人，多一颗
// 「整页打开」。下面先是它挂着的那条消息和从这里另开的任务，再是支线自己的对话和输入框。
//
// 对话就是频道和任务页用的那一栏（TopicChatColumn 的 `beside`），换了一段对话来读：
// 消息、连接、已读都走支线（或任务），名册和附件还是频道的。谁说话叫醒 AI 队友、输入
// 框写什么、等审阅时那一条，都由那一栏定，这里只管页头。桌面上它占频道页右边那一半，
// 手机上是一整页。
//
// AI 队友的一条回复上点「查看过程」，这一栏换成那一轮的现场，「返回支线」回来。支线的
// 对话只是藏起来，连接和滚动位置都还在。
import type { Block, ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { Thread } from '@/types/threads'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import { ApiError } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { threadHeading } from '@/lib/blockDisplay'
import { taskPhraseLabel } from '@/lib/board'
import { openThreadDocument } from '@/lib/libraryDocumentNavigation'
import { taskTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { roomTaskQuery, threadQuery } from '@/query/room'
import { currentUserName } from '@/services/account'
import { useWorkspaceStore } from '@/stores/workspace'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'

defineOptions({ name: 'ThreadPane' })

const props = defineProps<{
  room: Topic
  threadId: string
  members: ProjectMemberRow[]
  topicList: Topic[]
  memberNames: Record<string, string>
  /** 整页（手机）：没有关闭键，返回在顶栏。 */
  page?: boolean
  /** 地址点名的支线里那一条（`?block=`）：打开时停在它上面。 */
  focusBlock?: string | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-task', taskId: string): void
  /** 这一页此刻叫什么：转成任务后是任务名。 */
  (e: 'title', title: string): void
  /** 整页打开这条支线转成的任务：任务页，右边是概览、改动、预览。 */
  (e: 'open-page', taskId: string): void
  /** 一条消息转为任务：页头那颗是支线挂着的那条（这条支线原地转），支线里的一条回复
   *  是另开一件。 */
  (e: 'to-task', blockId: string): void
  (e: 'open-file', path: string): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()

const router = useRouter()
const store = useWorkspaceStore()
const viewer = computed(() => currentUserName.value ?? myHandle())

// A thread occupies the room's panel, and on phones there is no WorkPanel.
// Open its library-document cards on the document's own page instead, and a
// task's own document (「编辑了文档」) on the task's page, where 概览 shows it.
function openResource(resource: string, _turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) {
  if (resource !== 'doc') return
  if (document) return void openThreadDocument(router, props.room.project_id, document, review)
  if (task.value) emit('open-page', task.value.id)
}
// 正在看哪一轮的过程；null 是在看支线本身。
const processTurn = ref<string | null>(null)
const busy = ref(false)

// 这一条支线，和它挂着的那条消息（页头画的就是它）。第 2 个参数交给缓存：手上已经有
// 那块消息（`query/room` 的 `listedThread`，从频道那段对话的窗口里找）时，页头立刻
// 就有 —— 以前它只存在这个组件的一个 ref 里，谁都没存，所以每次打开都要等读回来。
const threadRead = useQuery(
  computed(() => threadQuery(props.threadId)),
  queryClient
)
const thread = computed<Thread | null>(() => threadRead.data.value ?? null)
const error = computed<string | null>(() => {
  const failed = threadRead.error.value
  if (!failed) return null
  return failed instanceof ApiError && failed.status === 404
    ? t('work.room.thread.notFound')
    : t('work.room.thread.loadFailed')
})
// 打开就算读过：支线的未读只对说过话的人算，读过了概览里那个点就该灭。
watch(
  () => props.threadId,
  (id) => {
    processTurn.value = null
    store.markRead(id)
  },
  { immediate: true }
)

const root = computed<Block | null>(() => thread.value?.root ?? null)
// 这是一件任务（支线原地转成的、另开的、新建的）时：任务名、状态、负责人写在页头。
const taskRead = useQuery(
  computed(() => {
    const id = thread.value?.task ?? ''
    return { ...roomTaskQuery(id), enabled: !!id }
  }),
  queryClient
)
const task = computed<RoomTask | null>(() => (thread.value?.task ? taskRead.data.value ?? null : null))
// The 支线's messages: each `<@handle>` in them told its person, drawn as the @chip.
const refs = computed(() => ({
  mentionNames: props.memberNames,
  topicTitles: {} as Record<string, string>,
  notified: true,
}))
function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
// 这个人的头像图：从成员名册查他挑过的素材 id。名册上没有他、或者他从没挑过
// （avatar_id 是 null）都给空串，让 UserAvatar 画彩色首字母——别再退回全站默认脸。
function avatarOf(handle: string): string {
  return getAvatarUrl(props.members.find((m) => m.user_handle === handle)?.avatar_id)
}
// 支线转成了任务：页面标题（手机上的顶栏）跟着写任务名，不再写「支线」。
watch(task, (made) => {
  if (made) emit('title', taskTitle(made))
})
// 归档的频道只能看。
const composerClosed = computed(() => (props.room.status === 'archived' ? t('work.channel.archivedNotice') : null))
// 页头那一行：转成任务后是任务名，之前是它挂着的那条消息说的事。
const heading = computed(() => {
  if (task.value) return taskTitle(task.value)
  return root.value ? threadHeading(root.value, refs.value) : ''
})

function toTask() {
  if (!root.value || busy.value) return
  busy.value = true
  emit('to-task', root.value.id)
  // 转成以后页头跟着换成任务那一行；没转成（失败了）就让按钮回来。
  setTimeout(() => (busy.value = false), 1500)
}
</script>

<template>
  <section
    v-guide-anchor="'thread-pane'"
    class="thread-pane"
    :aria-label="t('work.room.thread.title')"
    data-testid="thread-pane"
  >
    <!-- 页头一行，各样东西各自成一格，不拿点号串成一句。 -->
    <header v-guide-anchor="task ? 'task-pane' : ''" class="thread-pane__head">
      <v-icon v-if="task" size="18" class="thread-pane__icon" aria-hidden="true">mdi-checkbox-marked-outline</v-icon>
      <h2 class="thread-pane__title t-title" :title="heading">{{ heading }}</h2>
      <template v-if="task">
        <span class="thread-pane__state" data-testid="thread-task-phrase">{{ taskPhraseLabel(task, viewer) }}</span>
        <span class="thread-pane__gap" />
        <span v-if="task.owner_handle" class="thread-pane__owner">
          <UserAvatar :size="20" :name="nameOf(task.owner_handle)" :avatar="avatarOf(task.owner_handle)" />
          <span class="thread-pane__owner-name">{{
            t('work.task.ownerChip', { name: nameOf(task.owner_handle) })
          }}</span>
        </span>
        <BaseButton
          v-guide-anchor="'task-open-page'"
          icon="mdi-arrow-expand"
          size="sm"
          :title="t('work.room.thread.openPage')"
          :aria-label="t('work.room.thread.openPage')"
          data-testid="thread-open-page"
          @click="emit('open-page', task.id)"
        />
      </template>
      <template v-else>
        <span class="thread-pane__gap" />
        <BaseButton
          v-if="root && room.status !== 'archived'"
          kind="secondary"
          size="sm"
          :loading="busy"
          data-testid="thread-to-task"
          @click="toTask"
          >{{ t('work.room.thread.toTask') }}</BaseButton
        >
      </template>
      <BaseButton
        v-if="!page"
        icon="mdi-close"
        size="sm"
        :title="t('work.room.thread.close')"
        :aria-label="t('work.room.thread.close')"
        @click="emit('close')"
      />
    </header>

    <p v-if="error" class="thread-pane__error t-meta" role="alert">{{ error }}</p>
    <template v-else>
      <TopicChatColumn
        v-show="!processTurn"
        class="thread-pane__chat"
        beside
        :topic="room"
        :task-id="task ? task.id : null"
        :thread-id="task ? null : threadId"
        :task-agent-handle="task ? task.agent_handle ?? thread?.teammate ?? null : thread?.teammate ?? null"
        :focus-block="focusBlock ?? null"
        :composer-closed="composerClosed"
        :ask-closed="room.status === 'archived' || task?.status === 'closed'"
        :members="members"
        :topic-list="topicList"
        :member-names="memberNames"
        @open-file="emit('open-file', $event)"
        @open-resource="openResource"
        @open-topic="emit('open-topic', $event)"
        @open-card="emit('open-task', $event)"
        @mention-click="emit('mention-click', $event)"
        @upgrade-message="emit('to-task', $event as string)"
        @open-process="processTurn = $event"
        @review="task && emit('open-page', task.id)"
      />
      <template v-if="processTurn">
        <div class="thread-pane__process-head">
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-arrow-left"
            data-testid="thread-process-back"
            @click="processTurn = null"
            >{{ t('work.room.thread.backToThread') }}</BaseButton
          >
          <span class="t-meta">{{ t('work.room.thread.process') }}</span>
        </div>
        <PanelSiteHost
          class="thread-pane__chat"
          :topic-id="threadId"
          :project-id="room.project_id"
          active
          :member-names="memberNames"
          :agent-name="store.agentName"
          :only-turn="processTurn"
          @open-file="emit('open-file', $event)"
          @open-topic="emit('open-topic', $event)"
          @mention-click="emit('mention-click', $event)"
        />
      </template>
    </template>
  </section>
</template>

<style scoped>
.thread-pane {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  background: var(--surface);
}
.thread-pane__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--line);
}
.thread-pane__icon {
  flex: none;
  color: var(--muted);
}
.thread-pane__title {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.thread-pane__gap {
  flex: 1 1 0;
}
/* 和任务页页头那一格同一个样子：同一件任务，两处写法一样。 */
.thread-pane__state {
  display: inline-flex;
  flex: none;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.thread-pane__owner {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
  color: var(--text);
  font-size: 13px;
}
.thread-pane__owner-name {
  white-space: nowrap;
}
.thread-pane__error {
  padding: 16px;
  color: var(--muted);
}
.thread-pane__chat {
  flex: 1;
  min-height: 0;
}
.thread-pane__process-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
}
</style>
