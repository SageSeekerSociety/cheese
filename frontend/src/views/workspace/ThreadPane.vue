<script setup lang="ts">
// 一条支线：页头写「支线」和它在哪个频道、挂着哪条消息，右边是「转为任务」（一条支线
// 可以转出几件任务）；下面先是它挂着的那条消息和从这里出来的任务，再是支线自己的对话
// 和输入框。
//
// 对话就是频道那一栏，换了一段对话来读（`conversationId` 是支线的 id）：消息、连接、
// 已读都走支线，名册和附件还是频道的。桌面上它占频道页右边那一半，手机上是一整页。
//
// AI 队友的一条回复上点「查看过程」，这一栏换成那一轮的现场，「返回支线」回来。支线的
// 对话只是藏起来，连接和滚动位置都还在。
import type { Block, ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { Thread } from '@/types/threads'

import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { useMessageFold } from '@/composables/useMessageFold'

import { ApiError } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import AgentFeedbackCard from '@/components/feedback/AgentFeedbackCard.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { isAgentBlock } from '@/lib/authorship'
import { replySnippet } from '@/lib/blockDisplay'
import { taskLine } from '@/lib/channelTasks'
import { renderPlain } from '@/lib/renderMessage'
import { topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { roomTasksQuery, threadQuery } from '@/query/room'
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
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-task', taskId: string): void
  (e: 'to-task', rootBlockId: string): void
  (e: 'open-file', path: string): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()

const store = useWorkspaceStore()
const viewer = computed(() => currentUserName.value ?? myHandle())
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
// 挂着的那条消息长了也收起来：它长起来，支线自己的回复就被推到屏幕外。判据、量高和那颗
// 「展开 / 收起」都是频道消息行用的同一份（composables/useMessageFold）。它是已经落库的
// 消息，一直可以收。
const rootFoldEl = ref<HTMLElement | null>(null)
const rootFoldToggleEl = ref<HTMLElement | null>(null)
const {
  foldable: rootFoldable,
  expanded: rootExpanded,
  clamped: rootClamped,
  foldPx: rootFoldPx,
  toggle: toggleRootFold,
} = useMessageFold(
  rootFoldEl,
  computed(() => true),
  computed(() => root.value?.content),
  // 展开后那颗「收起」在正文末尾，正文高过一屏就掉到视野外：交给它自己把按钮带回来。
  rootFoldToggleEl
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
const refs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} as Record<string, string> }))
function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
// 这个人的头像图：从成员名册查他挑过的素材 id。名册上没有他、或者他从没挑过
// （avatar_id 是 null）都给空串，让 UserAvatar 画彩色首字母——别再退回全站默认脸。
function avatarOf(handle: string): string {
  return getAvatarUrl(props.members.find((m) => m.user_handle === handle)?.avatar_id)
}
const subtitle = computed(() => {
  const head = `#${topicTitle(props.room)}`
  return root.value
    ? `${head} · ${t('work.room.thread.lastReply', { name: nameOf(root.value.author), text: replySnippet(root.value, refs.value, 40) })}`
    : head
})
const rootTime = computed(() =>
  root.value ? new Date(root.value.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
)
const tasks = computed(() => {
  const id = root.value?.id
  return madeHere.value
    .filter((task) => task.upgraded_from_block_id === id)
    .map((task) => taskLine(task, viewer.value, nameOf))
})

function toTask() {
  if (!root.value || busy.value) return
  busy.value = true
  emit('to-task', root.value.id)
  // 转出去以后这一页跟着去任务页；没去成（失败了）就让按钮回来。
  setTimeout(() => (busy.value = false), 1500)
}
</script>

<template>
  <section class="thread-pane" :aria-label="t('work.room.thread.title')" data-testid="thread-pane">
    <header class="thread-pane__head">
      <div class="thread-pane__titles">
        <h2 class="thread-pane__title t-title">{{ t('work.room.thread.title') }}</h2>
        <div class="thread-pane__sub t-meta">{{ subtitle }}</div>
      </div>
      <BaseButton v-if="root" kind="secondary" size="sm" :loading="busy" data-testid="thread-to-task" @click="toTask">{{
        t('work.room.thread.toTask')
      }}</BaseButton>
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
        hide-header
        show-composer
        :composer-closed="room.status === 'archived' ? t('work.channel.archivedNotice') : null"
        :ask-closed="room.status === 'archived'"
        in-thread
        :members="members"
        :topic-list="topicList"
        @open-file="emit('open-file', $event)"
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
          <article v-if="root" class="thread-root">
            <!-- 队友一律 CheeseAvatar（和消息行同一套标记）；人走 UserAvatar：挑过头像的显示
                 头像，没挑过就按 handle 取色画首字母（颜色跟 handle，不跟昵称）。 -->
            <CheeseAvatar
              v-if="isAgentBlock(root)"
              class="thread-root__avatar"
              :size="28"
              :name="nameOf(root.author)"
              :handle="root.author"
            />
            <UserAvatar
              v-else
              class="thread-root__avatar"
              :size="28"
              :name="nameOf(root.author)"
              :avatar="avatarOf(root.author)"
              :seed="root.author"
            />
            <div class="thread-root__body">
              <div class="thread-root__head">
                <span class="thread-root__name">{{ nameOf(root.author) }}</span>
                <span class="t-meta thread-root__time">{{ rootTime }}</span>
              </div>
              <div
                ref="rootFoldEl"
                class="fold"
                :class="{ 'fold-clamped': rootClamped }"
                :style="rootClamped ? { maxHeight: `${rootFoldPx}px` } : undefined"
              >
                <MarkdownView
                  v-if="isAgentBlock(root)"
                  class="thread-root__text"
                  :source="root.content"
                  as="chat"
                  :names="refs"
                />
                <div
                  v-else
                  class="thread-root__text thread-root__text--plain"
                  v-html="renderPlain(root.content, refs)"
                />
                <div v-if="rootClamped" class="fold-fade" aria-hidden="true" />
              </div>
              <button
                v-if="rootFoldable"
                ref="rootFoldToggleEl"
                type="button"
                class="fold-toggle"
                :aria-expanded="rootExpanded"
                data-testid="thread-root-fold"
                @click="toggleRootFold"
              >
                <v-icon size="16">{{ rootExpanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
                {{ rootExpanded ? t('work.room.message.showLess') : t('work.room.message.showMore') }}
              </button>
            </div>
          </article>
          <div v-if="tasks.length" class="thread-pane__tasks">
            <TaskCard
              v-for="task in tasks"
              :key="task.id"
              :task="task"
              :owner-name="task.owner ? nameOf(task.owner) : null"
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

<style scoped src="@/styles/message-fold.css"></style>

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
  padding: 10px 12px 10px 16px;
  border-bottom: 1px solid var(--line);
}
.thread-pane__titles {
  flex: 1;
  min-width: 0;
}
.thread-pane__title {
  margin: 0;
}
.thread-pane__sub {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--muted);
}
.thread-pane__error {
  padding: 16px;
  color: var(--muted);
}
.thread-root {
  display: flex;
  gap: 10px;
  padding: 12px 16px 4px;
}
.thread-root__avatar {
  flex: none;
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
.thread-root__body {
  min-width: 0;
  flex: 1;
}
.thread-root__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.thread-root__name {
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
}
.thread-root__time {
  color: var(--faint);
}
.thread-root__text {
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
  overflow-wrap: anywhere;
}
.thread-root__text--plain {
  white-space: pre-wrap;
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
