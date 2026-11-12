<script setup lang="ts">
// 一条支线：页头一行，写它挂着的那条消息说的事，右边是「转为任务」——这段讨论原地变成
// 任务，还是这段对话、还是这位 AI 队友。下面先是它挂着的那条消息和从这里另开的任务，再是
// 支线自己的对话和输入框。读回来是一件任务（支线原地转成的、另开的、新建的）时不在这里
// 画：交给外面换成任务页（`as-task`），和侧栏任务行点开的是同一处。
//
// 对话就是频道和任务页用的那一栏（TopicChatColumn 的 `beside`），换了一段对话来读：
// 消息、连接、已读都走支线，名册和附件还是频道的。谁说话叫醒 AI 队友、输入
// 框写什么、等审阅时那一条，都由那一栏定，这里只管页头（ThreadHead）。桌面上它占频道页
// 右边那一半，页头在这一栏顶上；手机上是一整页，页头是顶栏的内容。
//
// AI 队友的一条回复上点「查看过程」，这一栏换成那一轮的现场，「返回支线」回来。支线的
// 对话只是藏起来，连接和滚动位置都还在。
import type { Block, ProjectMemberRow, Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'
import type { Thread } from '@/types/threads'

import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'

import { useChannelSpeakers } from '@/composables/useChannelSpeakers'
import { useScreenHeader } from '@/composables/useScreenHeader'

import { ApiError } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import ThreadHead from '@/components/room/ThreadHead.vue'
import PanelSiteHost from '@/components/work/PanelSiteHost.vue'
import { t } from '@/i18n'
import { threadHeading } from '@/lib/blockDisplay'
import { openThreadDocument } from '@/lib/libraryDocumentNavigation'
import { proposalPage } from '@/lib/proposalNavigation'
import { queryClient } from '@/query/client'
import { threadQuery } from '@/query/room'
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
  /** 一条消息转为任务：页头那颗是支线挂着的那条（这条支线原地转），支线里的一条回复
   *  是另开一件。转完（成没成）它才返回，按钮的忙碌跟着它。 */
  toTask: (blockId: string) => Promise<unknown>
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-task', taskId: string): void
  /** 读回来这是一件任务：在任务页上看它。 */
  (e: 'as-task', taskId: string): void
  (e: 'open-file', path: string): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()

const router = useRouter()
const store = useWorkspaceStore()

// A thread occupies the room's panel, and on phones there is no WorkPanel.
// Open its library-document cards on the document's own page instead.
function openResource(resource: string, _turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) {
  // 提议的技能、例行事务：支线占着面板，去它在项目里的那一页。
  const page = document ? proposalPage(resource, props.room.project_id, document.id) : null
  if (page) return void router.push(page)
  if (resource === 'doc' && document) openThreadDocument(router, props.room.project_id, document, review)
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
// 是一件任务（打开时已经是，或开着时被人转成了）：任务页上看它。
watch(
  () => thread.value?.task,
  (taskId) => {
    if (taskId) emit('as-task', taskId)
  },
  { immediate: true }
)
// The 支线's messages: each `<@handle>` in them told its person, drawn as the @chip.
const refs = computed(() => ({
  mentionNames: props.memberNames,
  topicTitles: {} as Record<string, string>,
  notified: true,
}))
// 归档的频道只能看。
// 主线只让部分角色发言、支线也不让人人回时，这里同样只读（#4795）。
const speakers = useChannelSpeakers(() => props.room)
const composerClosed = computed(() =>
  props.room.status === 'archived' ? t('work.channel.archivedNotice') : speakers.notice(true)
)
// 页头那一行：它挂着的那条消息说的事。
const heading = computed(() => (root.value ? threadHeading(root.value, refs.value) : ''))

// 转成了就已经去了任务页；没转成（失败了）按钮回来。
async function convert() {
  if (!root.value || busy.value) return
  busy.value = true
  try {
    await props.toTask(root.value.id)
  } finally {
    busy.value = false
  }
}
// 页头那一行。桌面上画在这一栏顶上；手机上这一页就是一整屏，这一行交给顶栏
// （useScreenHeader），页里不再写第二遍。还没读回来（或读不到）时不交，顶栏写路由的
// 标题「支线」。
const headBinding = computed(() => ({
  heading: heading.value,
  canToTask: !!root.value && props.room.status !== 'archived',
  busy: busy.value,
  closable: !props.page,
  onToTask: convert,
  onClose: () => emit('close'),
}))
useScreenHeader(() =>
  props.page && heading.value
    ? { content: { component: ThreadHead, props: { ...headBinding.value, bar: true } } }
    : null
)
</script>

<template>
  <section class="thread-pane" :aria-label="t('work.room.thread.title')" data-testid="thread-pane">
    <!-- Desktop: the head row tops this column. Phone: it is the top bar's content (useScreenHeader). -->
    <ThreadHead v-if="!page" v-bind="headBinding" />

    <p v-if="error" class="thread-pane__error t-meta" role="alert">{{ error }}</p>
    <template v-else>
      <TopicChatColumn
        v-show="!processTurn"
        class="thread-pane__chat"
        beside
        :topic="room"
        :thread-id="threadId"
        :task-agent-handle="thread?.teammate ?? null"
        :focus-block="focusBlock ?? null"
        :composer-closed="composerClosed"
        :ask-closed="room.status === 'archived'"
        :members="members"
        :topic-list="topicList"
        :member-names="memberNames"
        @open-file="emit('open-file', $event)"
        @open-resource="openResource"
        @open-topic="emit('open-topic', $event)"
        @open-card="emit('open-task', $event)"
        @mention-click="emit('mention-click', $event)"
        @upgrade-message="props.toTask($event as string)"
        @open-process="processTurn = $event"
      />
      <template v-if="processTurn">
        <div class="thread-pane__process-head">
          <!-- 图标在字前面：`icon` 是纯图标按钮的写法，给了它 BaseButton 就不画插槽里的
               字，这一颗会变成一颗没有名字的箭头。 -->
          <BaseButton
            kind="ghost"
            size="sm"
            prepend-icon="mdi-arrow-left"
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
