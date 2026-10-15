<script setup lang="ts">
// 一条支线：页头一行，写它挂着的那条消息说的事，右边是「转为任务」——这段讨论原地变成
// 任务，还是这段对话、还是这位 AI 队友。转成以后页头写任务名、状态和负责人，多一颗
// 「整页打开」。下面先是它挂着的那条消息和从这里另开的任务，再是支线自己的对话和输入框。
//
// 对话就是频道那一栏，换了一段对话来读（`conversationId` 是支线的 id）：消息、连接、
// 已读都走支线，名册和附件还是频道的。桌面上它占频道页右边那一半，手机上是一整页。
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
import ChatPanel from '@/components/ChatPanel.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import AgentFeedbackCard from '@/components/feedback/AgentFeedbackCard.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import ThreadRoot from '@/components/room/ThreadRoot.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { threadHeading } from '@/lib/blockDisplay'
import { taskPhraseLabel } from '@/lib/board'
import { taskLine } from '@/lib/channelTasks'
import { openThreadDocument } from '@/lib/libraryDocumentNavigation'
import { taskTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { roomTaskQuery, roomTasksQuery, threadQuery } from '@/query/room'
import { currentUserName } from '@/services/account'
import { useWorkspaceStore } from '@/stores/workspace'

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
// Open its library-document cards on the document's own page instead.
function openResource(resource: string, _turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) {
  if (resource !== 'doc' || !document) return
  void openThreadDocument(router, props.room.project_id, document, review)
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
// 这条支线原地转成的任务：任务名、状态、负责人写在页头。它的 id 就是支线的 id。
const taskRead = useQuery(
  computed(() => {
    const id = thread.value?.task ?? ''
    return { ...roomTaskQuery(id), enabled: !!id }
  }),
  queryClient
)
const task = computed<RoomTask | null>(() => (thread.value?.task ? taskRead.data.value ?? null : null))
// 负责人和协作者说的话就是说给 AI 队友的，不用 @；别人说的话它当参考，要它回答就 @ 它。
const takesPart = computed(
  () =>
    !!task.value &&
    (task.value.owner_handle === viewer.value || (task.value.contributor_handles ?? []).includes(viewer.value))
)
// 从这条支线挂着的那条消息拆出去的任务，和频道主线上那条消息下面的卡是同一份：只读
// 这一条带着的（`blocks=`），不读整个频道的。
// 卡片是装饰：读不到就照旧画上一份。任务变了（频道推来的帧）这一份跟着重读
// （`query/changes`）。
const madeRead = useQuery(
  computed(() => {
    const rootId = root.value?.id
    return { ...roomTasksQuery(props.room.id, { limit: 0, blocks: rootId ? [rootId] : [] }), enabled: !!rootId }
  }),
  queryClient
)
const madeHere = computed<RoomTask[]>(() => madeRead.data.value?.data ?? [])
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
// 归档的频道只能看；转成的任务关了，这段对话也就不再说话。
const composerClosed = computed(() => {
  if (props.room.status === 'archived') return t('work.channel.archivedNotice')
  return task.value?.status === 'closed' ? t('work.task.closedNotice') : null
})
// 页头那一行：转成任务后是任务名，之前是它挂着的那条消息说的事。
const heading = computed(() => {
  if (task.value) return taskTitle(task.value)
  return root.value ? threadHeading(root.value, refs.value) : ''
})
// 从这里另开的任务，列在那条消息下面；这条支线自己转成的那件写在页头，不再列一遍。
const tasks = computed(() => {
  const id = root.value?.id
  return madeHere.value
    .filter((made) => made.upgraded_from_block_id === id && made.id !== props.threadId)
    .map((made) => taskLine(made, viewer.value, nameOf))
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
      <ChatPanel
        v-show="!processTurn"
        class="thread-pane__chat"
        :topic="room"
        :conversation-id="threadId"
        :focus-block="focusBlock ?? null"
        hide-header
        show-composer
        :composer-closed="composerClosed"
        :ask-closed="room.status === 'archived' || task?.status === 'closed'"
        in-thread
        :always-summon="takesPart"
        :hint="task ? t('work.room.thread.taskPlaceholder') : null"
        :agent-handle="task ? task.agent_handle ?? thread?.teammate ?? null : thread?.teammate ?? null"
        :members="members"
        :topic-list="topicList"
        @open-file="emit('open-file', $event)"
        @open-resource="openResource"
        @open-topic="emit('open-topic', $event)"
        @open-card="emit('open-task', $event)"
        @mention-click="emit('mention-click', $event)"
        @upgrade-message="emit('to-task', $event)"
        @open-process="processTurn = $event"
      >
        <!-- 这条支线挂着的那条消息（连同从这里转出的任务、回复条数）交给对话的滚动区：
             它跟着回复一起滚，而不是钉在面板顶上。钉着的话，长消息一展开就把回复挤没、
             把正文末尾那颗「收起」顶到看不见的地方——点开就再收不回来了。 -->
        <template #timeline-start>
          <ThreadRoot
            v-if="root"
            :root="root"
            :name="nameOf(root.author)"
            :avatar="avatarOf(root.author)"
            :refs="refs"
          />
          <div v-if="tasks.length" class="thread-pane__tasks">
            <TaskCard
              v-for="made in tasks"
              :key="made.id"
              :task="made"
              :owner-name="made.owner ? nameOf(made.owner) : null"
              in-list
              @open="emit('open-task', $event)"
            />
          </div>
          <div v-if="thread" class="thread-pane__divider t-meta">
            <span>{{ t('work.room.thread.replies', { count: thread.reply_count }) }}</span>
            <span class="thread-pane__rule" />
          </div>
        </template>
        <template #timeline-end>
          <AgentFeedbackCard :topic-id="threadId" />
        </template>
      </ChatPanel>
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
/* 从这里出来的任务：和消息正文对齐（左内边距 16 + 头像 28 + 间距 10），几行一块。 */
.thread-pane__tasks {
  display: flex;
  flex-direction: column;
  margin: 6px 16px 4px 54px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.thread-pane__divider {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 16px;
  color: var(--muted);
}
.thread-pane__rule {
  flex: 1;
  height: 1px;
  background: var(--line);
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
