<script setup lang="ts">
// 一条支线：页头一行，写它挂着的那条消息说的事，右边是「转为任务」——这段讨论原地变成
// 任务，还是这段对话、还是这位 AI 队友。转成以后页头写任务名、状态和负责人，多一颗
// 「整页打开」。下面先是它挂着的那条消息和从这里另开的任务，再是支线自己的对话和输入框。
//
// 对话就是频道和任务页用的那一栏（TopicChatColumn 的 `beside`），换了一段对话来读：
// 消息、连接、已读都走支线（或任务），名册和附件还是频道的。谁说话叫醒 AI 队友、输入
// 框写什么、等审阅时那一条，都由那一栏定，这里只管页头（ThreadHead）。桌面上它占频道页
// 右边那一半，页头在这一栏顶上；手机上是一整页，页头是顶栏的内容。
//
// AI 队友的一条回复上点「查看过程」，这一栏换成那一轮的现场，「返回支线」回来。支线的
// 对话只是藏起来，连接和滚动位置都还在。
import type { Block, ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { Thread } from '@/types/threads'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { useChannelSpeakers } from '@/composables/useChannelSpeakers'
import { vGuideAnchor } from '@/composables/useGuideAnchors'
import { useScreenHeader } from '@/composables/useScreenHeader'

import { ApiError } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import ThreadHead from '@/components/room/ThreadHead.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { threadHeading } from '@/lib/blockDisplay'
import { openThreadDocument } from '@/lib/libraryDocumentNavigation'
import { proposalPage } from '@/lib/proposalNavigation'
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
  // 提议的技能、例行事务：支线占着面板，去它在项目里的那一页。
  const page = document ? proposalPage(resource, props.room.project_id, document.id) : null
  if (page) return void router.push(page)
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
// 支线转成了任务：页面标题（浏览器标签上那一行）跟着写任务名，不再写「支线」。
watch(task, (made) => {
  if (made) emit('title', taskTitle(made))
})
// 归档的频道只能看。
// 主线只让部分角色发言、支线也不让人人回时，这里同样只读（#4795）。
const speakers = useChannelSpeakers(() => props.room)
const composerClosed = computed(() =>
  props.room.status === 'archived' ? t('work.channel.archivedNotice') : speakers.notice(true)
)
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
// 页头那一行。桌面上画在这一栏顶上；手机上这一页就是一整屏，这一行交给顶栏
// （useScreenHeader），页里不再写第二遍。还没读回来（或读不到）时不交，顶栏写路由的
// 标题「支线」。
const headBinding = computed(() => ({
  heading: heading.value,
  task: task.value,
  viewer: viewer.value,
  members: props.members,
  memberNames: props.memberNames,
  canToTask: !!root.value && props.room.status !== 'archived',
  busy: busy.value,
  closable: !props.page,
  onToTask: toTask,
  onOpenPage: (taskId: string) => emit('open-page', taskId),
  onClose: () => emit('close'),
}))
useScreenHeader(() =>
  props.page && heading.value
    ? { content: { component: ThreadHead, props: { ...headBinding.value, bar: true } } }
    : null
)
</script>

<template>
  <section
    v-guide-anchor="'thread-pane'"
    class="thread-pane"
    :aria-label="t('work.room.thread.title')"
    data-testid="thread-pane"
  >
    <!-- Desktop: the head row tops this column. Phone: it is the top bar's content (useScreenHeader). -->
    <ThreadHead v-if="!page" v-bind="headBinding" />

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
