<script setup lang="ts">
import type { AgentControlState, Block, ChatAttachment, ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'
import type { OpenedDocument } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { CardPhase } from '@/lib/topicState'
import type { ChapterRow } from '@/types/chapters'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { attachmentRawUrl, downloadFile } from '@/api/roomFiles'
import BaseButton from '@/components/base/BaseButton.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import AgentFeedbackCard from '@/components/feedback/AgentFeedbackCard.vue'
import TaskCard from '@/components/room/TaskCard.vue'
import ThreadRoot from '@/components/room/ThreadRoot.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import { t } from '@/i18n'
import { artifactName } from '@/lib/blockDisplay'
import { taskLine } from '@/lib/channelTasks'
import { topicTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { roomTaskQuery, roomTasksQuery, threadQuery } from '@/query/room'
import { currentUserName } from '@/services/account'
import { useWorkspaceStore } from '@/stores/workspace'

// 一段对话：时间线 + 输入框 + 贴在输入框上的采纳框 + 输入框旁边的 chips。
//
// 它是一个组件而不是 TopicView 模板里的一段，因为同一段对话要挂在好几个地方：频道
// 主线和任务页左边那一栏、手机上工作面板「对话」那一格，以及频道页右半边打开的支线
// 和任务（`beside`，外面套 ThreadPane 的页头）。同一份接线写两遍是这几处早晚长歪的
// 原因：谁说话叫醒 AI 队友、输入框写什么、任务等审阅时那一条，都只在这里定。
defineOptions({ name: 'TopicChatColumn' })

const props = defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  topicList: Topic[]
  // 开这个话题的那一刻还有多少条没读——对话栏用它画「以下是新消息」那条线。
  // 必须一路透传：漏掉它不会报错，只是那条线再也不出现。
  unreadOnOpen?: number
  /** 打开时停在这一条（地址里的 `?block=`）。 */
  focusBlock?: string | null
  /** 这一栏是房间里一个任务的对话：每句话都说给做它的 AI 队友，采纳卡是这个任务的，
   *  提议卡（技能、反馈）也按这个任务取——队友在哪儿提的，卡就落在哪儿。 */
  taskId?: string | null
  /** 这一栏是一条还没转成任务的支线。 */
  threadId?: string | null
  /** 这段对话的章节，按对话里的顺序（频道页、任务页给；支线不给）。 */
  chapters?: ChapterRow[]
  /** 现在能不能改章节：说得上话、没归档。 */
  chapterable?: boolean
  /** 已经钉成章节的那几条（block id）。 */
  chapterIds?: ReadonlySet<string>
  /** 开在频道页右半边（手机上是一整页）：对话最上面是它挂着的那条消息、从那条消息
   *  另开的任务和回复条数；不在任务页上时才有。 */
  beside?: boolean
  /** 发送框那个 @ 给谁：任务单独指定的队友，或支线里一直在答的那位。不给就是房间
   *  名册上那位。 */
  taskAgentHandle?: string | null
  /** 这个房间名册上每个 handle 叫什么。 */
  memberNames?: Record<string, string>
  /** 这里此刻不能说话的原因，见 ChatPanel。 */
  composerClosed?: string | null
  /** 这里的提问已经不再等回答，见 ChatPanel。 */
  askClosed?: boolean
  /** 采纳那一条此刻挂在别处（专注模式里它在面板底部），这里不再放一份。 */
  acceptElsewhere?: boolean
  /** 手机上：采纳那一条只放「审阅」，决定在「改动」页底部。 */
  acceptReviewButton?: boolean
  // 换过 AI 队友之后 +1，对话栏据此重拉名册（它显示的 AI 名字来自那份名册）。
  // 同样必须一路透传：漏掉它不报错，只是换完队友对话里还写着上一个的名字。
}>()

const emit = defineEmits<{
  (e: 'open-room'): void
  (e: 'turn-done'): void
  // 芝士 开工 / 收工。必须一路透传：右边那格「现场」靠它在开工那一刻出现。
  (e: 'working', working: boolean): void
  // 会话状态的那一帧。同样一路透传给现场那格的会话详情。
  (e: 'agent-control', state: AgentControlState): void
  // 现场时间线上新到或变了的一行、在跑的轮次各自的开始时间：一路透传给现场那格。
  // 漏掉不报错，只是现场又回到「打开才刷新」。
  (e: 'site-block', block: Block): void
  (e: 'site-turns', turns: Record<string, number>): void
  // 谁在这个房间里忙：现场那一格画同一份。
  (e: 'activity', lines: MemberActivityLine[]): void
  // `id` 是后端指名的那一行（topics 帧上是房间 id）；两个都要转，漏掉的话刷新又退回整份重下。
  (e: 'state-changed', resource: string, id?: string): void
  // 芝士摆出来一份东西：面板立刻看一眼当前预览。必须一路透传，漏掉的话「预览」
  // 那一格又回到等轮询。
  (e: 'preview-shown'): void
  // 消息的 ⋯ 里钉成章节/取消章节，或书签条里取消：这一页拿着这段对话的地址，由它去改。
  (e: 'chapter', block: Block): void
  (e: 'unchapter', target: Block | string): void
  (e: 'rename', blockId: string, title: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  // 参数都要转：`turnId` 决定文档面板高亮哪一轮改的段落，`review` 是「查看改动」要标出
  // 的那几处；只转第一个的话这两样都会静默降级成「整篇闪一下」。
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
  (e: 'upgrade-message', payload: unknown): void
  // 「在支线中回复」、点开消息下面那一行：页面换到那条支线。
  (e: 'open-thread', block: Block): void
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string): void
  // AI 队友一条回复上的「查看过程」：右半边换成那一轮的现场。
  (e: 'open-process', turnId: string): void
  // 话题此刻处在哪一段，由采纳框说了算——头部的状态词和面板开在哪一格都读它。
  (e: 'phase', phase: CardPhase): void
  // 采纳框上的「去看改动」：面板换到 改动 那一格。
  (e: 'review'): void
}>()

const chatRef = ref<{
  linkDown: boolean
  send: (content: string, summon: boolean, attachments?: ChatAttachment[]) => boolean
  submitQuestion: SubmitPreviewQuestion
} | null>(null)
const feedbackRef = ref<{ reload: () => Promise<void> } | null>(null)
// 正在写退回理由：输入框让给退回那一块（AcceptRejectForm），写到一半的消息留在原处。
const rejecting = ref(false)

// 卡片属于提出它的那段对话：在任务里就是这个任务，否则是房间本身。三种卡都按它
// 取、按它决定 —— 任务里提的卡落在任务里，按房间去取就一张也看不到。
const conversationId = computed(() => props.taskId ?? props.threadId ?? props.topic.id)
const store = useWorkspaceStore()

// 这一栏是任务时，那件任务（任务页、右半边页头读的是同一份）。
const taskRead = useQuery(
  computed(() => ({ ...roomTaskQuery(props.taskId ?? ''), enabled: !!props.taskId })),
  queryClient
)
const task = computed<RoomTask | null>(() => (props.taskId ? taskRead.data.value ?? null : null))
const viewer = computed(() => currentUserName.value ?? myHandle())
const closed = computed(() => task.value?.status === 'closed')
// 负责人和协作者在开着的任务里说的话就是说给 AI 队友的，不用 @；别人说的话它当参考，
// 要它回答就 @ 它。任务关了，和频道主线一样，@ 了才答。
const alwaysSummon = computed(
  () =>
    !!task.value &&
    !closed.value &&
    (task.value.owner_handle === viewer.value || (task.value.contributor_handles ?? []).includes(viewer.value))
)
const hint = computed(() => {
  if (closed.value) return t('work.task.closedHint')
  return props.beside && task.value ? t('work.room.thread.taskPlaceholder') : null
})

// 它挂着的那条主线消息：右半边打开的支线和任务都有；任务页上只有支线原地转成的那件
// 有，前面写它来自哪个频道（另开的任务，来源在「概览」顶上那一行）。
const fromThread = computed(() => !!task.value?.from_thread)
const threadRead = useQuery(
  computed(() => {
    const id = props.beside || fromThread.value ? props.taskId ?? props.threadId ?? '' : ''
    return { ...threadQuery(id), enabled: !!id }
  }),
  queryClient
)
const thread = computed(() => threadRead.data.value ?? null)
const root = computed<Block | null>(() => (props.beside || fromThread.value ? thread.value?.root ?? null : null))
// 从那条消息本身另开的任务，列在它下面；从支线里某句回复拆出去的，挂在那句下面
// （ChatPanel）；这段对话自己转成的那件写在页头，不再列一遍。
const madeRead = useQuery(
  computed(() => {
    const rootId = props.beside ? root.value?.id : undefined
    return { ...roomTasksQuery(props.topic.id, { limit: 0, blocks: rootId ? [rootId] : [] }), enabled: !!rootId }
  }),
  queryClient
)
const madeHere = computed(() => {
  const id = root.value?.id
  return (madeRead.data.value?.data ?? [])
    .filter(
      (made) =>
        made.upgraded_from_block_id === id &&
        made.id !== conversationId.value &&
        (!made.source_block_id || made.source_block_id === id)
    )
    .map((made) => taskLine(made, viewer.value, nameOf, madeAgentName(made)))
})
// The message it hangs under: each `<@handle>` in it told its person, drawn as the @chip.
const refs = computed(() => ({
  mentionNames: props.memberNames ?? {},
  topicTitles: {} as Record<string, string>,
  notified: true,
}))
/** 做这件任务的队友叫什么：它单独指定的那位，否则这间房名册上的队友，再否则项目的默认队友。 */
function madeAgentName(made: RoomTask): string {
  const seat = made.agent_handle || props.topic.agent_handle || props.taskAgentHandle
  return (seat && props.memberNames?.[seat]) || store.agentName
}

function nameOf(handle: string): string {
  return props.memberNames?.[handle] || handle
}
function avatarOf(handle: string): string {
  return getAvatarUrl(props.members.find((m) => m.user_handle === handle)?.avatar_id)
}

/** 支线顶部那条原消息带的文件：下载在这里做，画消息的那一层只发一个意图。
 *
 *  成功和失败的话都在 `downloadFile` 那一层说（`lib/download`），这里不再补一句。 */
async function downloadRootFile(block: Block) {
  await downloadFile(attachmentRawUrl(props.topic.id, block.content), artifactName(block))
}

// 没加入的频道：输入框的位置是一句话加「加入频道」，加入之后输入框回来。
const joining = ref(false)
async function join() {
  joining.value = true
  try {
    await store.setJoined(props.topic.id, true)
  } finally {
    joining.value = false
  }
}

const linkDown = computed(() => !!chatRef.value?.linkDown)
const submitQuestion: SubmitPreviewQuestion = (request) => chatRef.value?.submitQuestion(request) ?? false

defineExpose({
  linkDown,
  reloadFeedback: () => feedbackRef.value?.reload(),
  // 普通定位沿用聊天提交；图上画过东西时随行带那张合成图。明确的整页 AI 提问由
  // submitQuestion 在正文点名。
  say: (content: string, attachments?: ChatAttachment[]) => chatRef.value?.send(content, true, attachments) ?? false,
  submitQuestion,
})
</script>

<template>
  <div class="chat-col">
    <ChatPanel
      ref="chatRef"
      :topic="topic"
      :conversation-id="taskId ?? threadId ?? null"
      :agent-handle="taskAgentHandle ?? null"
      :composer-closed="composerClosed"
      :hint="hint"
      :ask-closed="askClosed"
      :always-summon="alwaysSummon"
      :place-state="task?.status ?? null"
      :in-thread="beside"
      hide-header
      :show-composer="!rejecting"
      :members="members"
      :topic-list="topicList"
      :unread-on-open="unreadOnOpen"
      :focus-block="focusBlock"
      :chapters="chapters"
      :chapterable="chapterable"
      :chapter-ids="chapterIds"
      @chapter="emit('chapter', $event)"
      @unchapter="emit('unchapter', $event)"
      @rename="(id: string, title: string) => emit('rename', id, title)"
      @turn-done="emit('turn-done')"
      @working="emit('working', $event)"
      @agent-control="emit('agent-control', $event)"
      @site-block="emit('site-block', $event)"
      @site-turns="emit('site-turns', $event)"
      @activity="emit('activity', $event)"
      @state-changed="(resource: string, id?: string) => emit('state-changed', resource, id)"
      @preview-shown="emit('preview-shown')"
      @mention-click="emit('mention-click', $event)"
      @open-file="(path) => emit('open-file', path)"
      @open-resource="
        (resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) =>
          emit('open-resource', resource, turnId, review, document)
      "
      @upgrade-message="emit('upgrade-message', $event)"
      @open-thread="emit('open-thread', $event)"
      @open-topic="emit('open-topic', $event)"
      @open-card="emit('open-card', $event)"
      @open-process="emit('open-process', $event)"
    >
      <!-- 验收卡贴在输入框上方，不在时间线末尾：它是一个等人做的决定，要一直看得
           见，但平时只占一行，不把对话挤到只剩几行。 -->
      <template #above-composer>
        <TopicAcceptCard
          v-if="!acceptElsewhere && !threadId"
          class="chat-dock"
          :topic-id="topic.id"
          :task-id="taskId ?? undefined"
          :topic-status="topic.status"
          :review-button="acceptReviewButton"
          @phase="emit('phase', $event)"
          @review="emit('review')"
          @rejecting="rejecting = $event"
        />
      </template>
      <!-- 它挂着的那条消息交给对话的滚动区：跟着回复一起滚，而不是钉在顶上。钉着的话，
           长消息一展开就把回复挤没、把正文末尾那颗「收起」顶到看不见的地方。 -->
      <!-- No v-if on this <template>: a conditional slot makes ChatPanel's slots
           dynamic, and ChatPanel forwards them to ChatTimeline, which then redraws
           every row on each ChatPanel render — that is, on every keystroke in the
           composer. The branches inside already render nothing when neither holds. -->
      <template #timeline-start>
        <template v-if="beside">
          <ThreadRoot
            v-if="root"
            :root="root"
            :name="nameOf(root.author)"
            :avatar="avatarOf(root.author)"
            :refs="refs"
            :files="thread?.files"
            :topic-id="topic.id"
            @download="downloadRootFile($event)"
          />
          <div v-if="madeHere.length" class="made-here">
            <TaskCard
              v-for="made in madeHere"
              :key="made.id"
              :task="made"
              :owner-name="made.owner ? nameOf(made.owner) : null"
              in-list
              @open="emit('open-card', $event)"
            />
          </div>
          <div v-if="thread && root" class="replies-rule t-meta">
            <span>{{ t('work.room.thread.replies', { count: thread.reply_count }) }}</span>
            <span class="replies-rule__line" />
          </div>
        </template>
        <template v-else-if="root">
          <button type="button" class="from-room t-meta" @click="emit('open-room')">
            {{ t('work.task.fromRoom', { room: topicTitle(topic) }) }}
          </button>
          <ThreadRoot
            :root="root"
            :name="nameOf(root.author)"
            :avatar="avatarOf(root.author)"
            :refs="refs"
            :files="thread?.files"
            :topic-id="topic.id"
            @download="downloadRootFile($event)"
          />
          <div class="from-room__rule" aria-hidden="true" />
        </template>
      </template>
      <template #timeline-end>
        <!-- Agent 反馈卡：「这一轮结束时，平台要人做的一个决定」，接在这一轮的
             对话后面。
             什么时候出现由**服务端**说了算：它列出这个话题里还活着的提案卡
             （`GET /topics/{id}/feedback-proposals`），一张都没有就什么都不画。
             「不用」记在服务端（按指纹），所以拒绝过一次的问题不会因为刷新又回来；
             换个说法重提的会回来 —— 那是另一次提问，值得再问一遍。 -->
        <AgentFeedbackCard ref="feedbackRef" :topic-id="conversationId" />
      </template>
      <!-- 输入区那一行只放**这条消息**的动作，所以这里只剩话题的状态。谁在跑
         （AI 队友）和在哪跑（工作电脑）都不是某条消息的动作，摆在输入区上纯是占
         位置：队友进了成员名册（它本来就是这个房间的成员），工作电脑在话题头的 ⋯ 里。 -->
      <template #composer-closed>
        <BaseButton
          v-if="topic.joined === false && topic.status !== 'archived'"
          kind="primary"
          size="sm"
          :loading="joining"
          @click="join"
        >
          {{ t('work.channel.join') }}
        </BaseButton>
      </template>
      <template #composer-chips>
        <span v-if="topic.status === 'archived'" class="d-inline-flex align-center ga-1 c-faint archived-chip">
          <span class="status-dot status-dot--muted" />{{ t('work.sidebar.archived') }}
        </span>
      </template>
    </ChatPanel>
  </div>
</template>

<style scoped>
.from-room {
  align-self: flex-start;
  margin: 12px 16px 0 54px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.from-room:hover {
  color: var(--ink);
  text-decoration: underline;
}
.from-room__rule {
  height: 1px;
  margin: 8px 16px 4px;
  background: var(--line);
}
/* 从这里出来的任务：和消息正文对齐（左内边距 16 + 头像 28 + 间距 10），几行一块。 */
.made-here {
  display: flex;
  flex-direction: column;
  max-width: 620px;
  margin: 6px 16px 4px 54px;
  border-radius: var(--radius-md);
  background: var(--fill);
  overflow: hidden;
}
.replies-rule {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 16px;
  color: var(--muted);
}
.replies-rule__line {
  flex: 1;
  height: 1px;
  background: var(--line);
}
.archived-chip {
  font-size: 12px;
}
/* 和对话同一栏：时间线（ChatTimeline）、输入框（ChatPanel）各把自己收成一栏居中，
   贴在输入框上的这一条（验收卡）跟着收同一个值，不然它比上下两块都宽。桌面上是读
   的一栏 --page-w-read，手机外壳里是 --page-w，三块始终对齐。 */
.chat-dock {
  width: 100%;
  max-width: var(--page-w-read);
  margin-inline: auto;
}
@media (max-width: 959.98px) {
  .chat-dock {
    max-width: var(--page-w);
  }
}
.chat-col {
  /* 对话栏那条错误提示（ChatPanel 的 .chat-error-toast）是 absolute，定位的就是
     这一层——ChatPanel 自己的根不是定位元素。 */
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
</style>
