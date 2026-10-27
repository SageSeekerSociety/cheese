<script setup lang="ts">
// The room's chat column. It is a shell now: props in, one composable
// (useChatPanel) for everything the room is doing, and markup that draws.
//
// It used to be 1987 lines holding the socket, the history window, the
// composer, the row animations and the whole timeline at once. The seam is the
// one RoomMessage already draws around a row — a composable knows what the room
// is doing, a view knows what it looks like — and the views under ./chat are
// those pieces: header, timeline, new-message pill, error toast.
import type { Block, ProjectMemberRow, Topic } from '../cx_types'
import type { TaskLine } from '../lib/channelTasks'
import type { ChapterRow } from '../types/chapters'

import { computed, provide, ref, watch } from 'vue'

import { useAskGroupAnswer } from '../composables/useAskGroupAnswer'
import { useAskResponse } from '../composables/useAskResponse'
import { useChannelPins } from '../composables/useChannelPins'
import { type ChatPanelEmit, useChatPanel } from '../composables/useChatPanel'
import { vGuideAnchor } from '../composables/useGuideAnchors'
import { useKeepAudience } from '../composables/useKeepAudience'
import { useMentionOutside } from '../composables/useMentionOutside'
import { noteFacts, useProjectTour } from '../composables/useProjectTour'
import { useStartSteps } from '../composables/useStartSteps'
import { summonPrefill, useThreadLines } from '../composables/useThreadLines'
import { memberName } from '../lib/agentNames'
import { isAgentHandle } from '../lib/authorship'
import { isImageBlock } from '../lib/blockDisplay'
import { taskLine, tasksByOrigin } from '../lib/channelTasks'
import { CHAT_IMAGE_ACTIONS, handOffImageTool } from '../lib/chatImages'
import { createQuestionSubmit } from '../lib/previewQuestion'

import BookmarkRail from './chat/BookmarkRail.vue'
import ChatErrorToast from './chat/ChatErrorToast.vue'
import ChatNewMessagesPill from './chat/ChatNewMessagesPill.vue'
import ChatPanelHeader from './chat/ChatPanelHeader.vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import { useComposerShortcut } from './chat/composables/useComposerShortcut'
import MentionOutsidePrompt from './chat/MentionOutsidePrompt.vue'
import ErrorBoundary from './common/ErrorBoundary.vue'
import KeepToLibraryConfirm from './room/KeepToLibraryConfirm.vue'
import MemberActivity from './room/MemberActivity.vue'
import RoomComposer from './room/RoomComposer.vue'
import RoomMessageSheet from './room/RoomMessageSheet.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 私聊里的消息不能转为任务：不给「转为任务」。 */
    noUpgrade?: boolean
    /** 这是一段私聊：只有里面的人看得到，保存到资料库前要先确认。 */
    privateChat?: boolean
    // 这一栏里每条消息都是说给芝士听的：1:1 私聊那种只有它一个对话方的地方。
    // 别处叫它靠 @ 它（和 @ 人同一套），见 sendDraft。
    alwaysSummon?: boolean
    /** 和 AI 队友私聊时为 false：⌘/Ctrl+Enter 不「交给」谁，就是发送。 */
    handOff?: boolean
    // Render the composer at the bottom of THIS column. Every caller wants it —
    // 工作台 used to span its own copy across the chat and the work panel, which
    // read as addressing the whole topic while 99% of what it sent was a chat
    // message only this column shows. Still a prop, because the composer is
    // the last thing the root-topic embed would want if it ever loses its input.
    showComposer?: boolean
    // Show the GitHub-PR-style header (话题 = PR). Only real work topics are
    // PRs — the root topic (本体) and the 1:1 private chat are NOT, so they use
    // the plain chat header instead.
    prHeader?: boolean
    // No header at all. 工作台 puts ONE topic header above both columns (see
    // TopicHeader.vue), so the chat column must not draw a second one under it.
    hideHeader?: boolean
    // Project roster (handle→name) so <@handle> mention tokens render as chips.
    members?: ProjectMemberRow[]
    // Project topics (id→title) so <#topicId> reference tokens render as chips.
    topicList?: Topic[]
    // Header label override for a 私聊 whose stored title is a bookkeeping key
    // (e.g. a person DM's canonical "私聊 · a · b"): show the peer's name instead.
    titleOverride?: string | null
    // 标题左边那颗 ←，以及它旁边的字。私聊是从名册点进来的，而名册页在桌面上
    // 不是侧栏的一行，所以没有这颗按钮就只能靠浏览器后退回去。null = 不画。
    backLabel?: string | null
    // 开这个话题的那一刻还有多少条没读（只数别人发的，和侧栏角标同一口径）。
    // 由 host 在 markRead 之前捕获——一旦 markRead 跑过，这个数就没了。
    unreadOnOpen?: number
    // 打开时停在这一条（搜索结果、链接里的 `?block=`）。null = 停在平常的位置。
    focusBlock?: string | null
    // 读的是房间里的一个任务的对话，而不是房间自己的（见 useChatPanel 的 `place`）。
    conversationId?: string | null
    // 这段对话说给谁听，当它不是房间名册上那位时的 handle：任务单独指定的队友（任务
    // 信息卡那行「AI 队友」挑的），或支线正在和它说话的那位队友。发送框那个 @ 写它。
    // 空＝跟着房间。
    agentHandle?: string | null
    // 这里此刻不能说话，以及为什么（任务已关闭、频道已归档）。输入框的位置换成这一句，
    // `composer-closed` 插槽接在它后面。
    composerClosed?: string | null
    // 这里的提问已经不再等回答（任务关了、频道归档了）：选项不再给点。
    askClosed?: boolean
    // 这一栏是一条支线：输入框写「在支线中回复」；我上一句叫过 AI 队友的话，打开时
    // 先带上「@芝士 」。
    inThread?: boolean
    // 输入框里那句提示，当它不是这一栏默认的那句时：支线转成任务后写「在任务中回复」。
    hint?: string | null
    /** 这段对话的章节，按对话里的顺序（频道页、任务页给；支线、私聊不给）。 */
    chapters?: ChapterRow[]
    /** 现在能不能改章节：说得上话、没归档。 */
    chapterable?: boolean
    /** 已经钉成章节的那几条（block id）。 */
    chapterIds?: ReadonlySet<string>
  }>(),
  {
    alwaysSummon: false,
    handOff: true,
    showComposer: false,
    prHeader: false,
    hideHeader: false,
    members: () => [],
    topicList: () => [],
    titleOverride: null,
    backLabel: null,
    unreadOnOpen: 0,
    conversationId: null,
    chapters: () => [],
    chapterable: false,
    chapterIds: () => new Set<string>(),
    agentHandle: null,
    composerClosed: null,
    askClosed: false,
    inThread: false,
    hint: null,
  }
)

// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). The list itself — and what each
// event means — is ChatPanelEmit in ../composables/useChatPanel: the panel and
// whoever listens to it have to agree, so there is one declaration, not two.
const emit = defineEmits<ChatPanelEmit>()

// 频道的主线：消息可以有支线。私聊、任务、支线里都没有。
// 项目本体自己的那一栏：不是支线、不是任务页里的对话栏，房间也没归档。
const onProjectLine = computed(
  () => !props.conversationId && props.topic?.kind === 'root' && props.topic.status !== 'archived'
)
const mainLine = computed(() => !!props.topic && !props.noUpgrade && !props.conversationId)
// 已归档的频道只能看：不再开新的支线。
const threadable = computed(() => mainLine.value && props.topic?.status !== 'archived')
// 频道说它的支线变了：先把屏幕上那几行换新，再照常往上报（概览里的「支线」那一页也要读）。
const forward = emit as unknown as (event: string, ...args: unknown[]) => void
const panelEmit = ((event: string, ...args: unknown[]) => {
  if (event === 'state-changed' && args[0] === 'threads') void threadLines.refresh()
  if (event === 'thread-activity') {
    const [threadId, member, active] = args as [string, string, boolean]
    void threadLines.onActivity(threadId, member, active)
    return
  }
  if (event === 'thread-status') {
    const [threadId, record] = args as [string, Block]
    threadLines.onStatus(threadId, record)
    return
  }
  forward(event, ...args)
}) as ChatPanelEmit

// A composable is not re-run when a prop changes: it reads the current value
// when it needs it. That is why every prop is handed over as an accessor.
// 我 @ 了不在这个频道里的人：问要不要加进来（Slack 那样），见 useMentionOutside。
const mentionOutside = useMentionOutside({
  place: () => props.conversationId ?? props.topic?.id ?? null,
  nameOf: (handle) => memberName(props.members.find((m) => m.user_handle === handle)) || handle,
})
const { prompt: outsidePrompt, adding: outsideAdding } = mentionOutside

// 这一栏的根：时间线和输入区都在它里面。滚动位置的「重写」判定要观察它——#4662
// 把时间线包进了 `.timeline-zone`，滚动框的直接父元素已经不是包含输入区的那一层了。
const chatRoot = ref<HTMLElement | null>(null)

const panel = useChatPanel({
  topic: () => props.topic,
  conversationId: () => props.conversationId,
  column: () => chatRoot.value,
  agentHandle: () => props.agentHandle,
  alwaysSummon: () => props.alwaysSummon,
  answersInThread: () => threadable.value,
  showComposer: () => props.showComposer,
  members: () => props.members,
  topicList: () => props.topicList,
  unreadOnOpen: () => props.unreadOnOpen,
  focusBlock: () => props.focusBlock ?? null,
  onPosted: mentionOutside.offer,
  emit: panelEmit,
})

// 一件任务交给的那位 AI 队友叫什么；没单独指定的，是这间房的那位。
const taskAgentName = (handle: string | null | undefined) =>
  (handle && panel.refMaps.mentionNames[handle]) || panel.agentName.value
const threadLines = useThreadLines({
  mainLine: () => mainLine.value,
  roomId: () => props.topic?.id ?? null,
  timeline: panel.timeline,
  agentNameOf: taskAgentName,
})
// 置顶：只在频道主线上、我能在这里说话（加入了这个频道）、频道没归档时能做。
const pinnable = computed(() => threadable.value && props.topic?.joined !== false)
const pins = useChannelPins({
  channelId: () => (mainLine.value ? props.topic?.id ?? null : null),
  report: (message) => (panel.errorMsg.value = message),
  seen: () => panel.timeline.messages.value.some((m) => typeof m.meta?.pinned_block_id === 'string'),
})
// 章节：这段对话的目录由拿着这一段地址的页面取（频道页、任务页），这里只画。支线和
// 私聊不传，就没有章节刻度，也没有 ⋯ 里那一项。
// 私密频道和私聊里的东西只有这里的人看得到：保存到资料库之前先说清保存后谁会看到。
const keeping = ref<Block | null>(null)
const keepAudience = useKeepAudience(
  () => props.topic?.id ?? null,
  () => keeping.value !== null
)
// 存在哪段对话里按的，就在哪段对话里说「已存进资料库」：支线里按的报给支线，不报到
// 主线上去（后端按这个 id 找房间，也按它决定那句话落在哪）。
function keepIn(): string | null {
  return props.conversationId ?? props.topic?.id ?? null
}
function keepFile(block: Block) {
  if (!props.topic) return
  const at = keepIn()
  if (props.privateChat || props.topic.members_only) keeping.value = block
  else if (at) pins.keep(at, block)
}
function confirmKeep() {
  const block = keeping.value
  keeping.value = null
  const at = keepIn()
  if (block && at) pins.keep(at, block)
}
function nameOf(handle: string): string {
  return panel.nameOfHandle(handle)
}
// 频道里每件任务的那张卡：挂在它出自的那条消息下面，或者是「新建了任务」那一条；支线里
// 挂在拆出它的那句回复下面。认得的是跟着窗口里各块一起来的那几件（useTimelineTasks）。
const knownTasks = computed(() => [...panel.roomTasks.known.value.values()])
const taskLines = computed(
  () =>
    new Map(
      knownTasks.value.map((task) => [
        task.id,
        taskLine(task, panel.viewer.value, nameOf, taskAgentName(task.agent_handle)),
      ])
    )
)
const taskOrigins = computed(() => tasksByOrigin(knownTasks.value, props.conversationId))
const NO_TASKS: TaskLine[] = []
const tasksByBlock = computed(() => {
  const map = new Map<string, TaskLine[]>()
  for (const [blockId, tasks] of taskOrigins.value) {
    const lines = tasks.flatMap((task) => taskLines.value.get(task.id) ?? [])
    if (lines.length) map.set(blockId, lines)
  }
  return map
})
function taskOf(taskId: string): TaskLine | null {
  return taskLines.value.get(taskId) ?? null
}
function tasksUnder(blockId: string): TaskLine[] {
  return tasksByBlock.value.get(blockId) ?? NO_TASKS
}

const {
  // `topic` itself is NOT destructured: the prop of the same name already holds
  // the same value (the panel's accessor is a computed over it), and a setup
  // binding shadowing a prop is an error here (vue/no-dupe-keys).
  agentName,
  agentSeat,
  awaitingReply,
  agentFaces,
  activityLines,
  prShortId,
  prState,
  composerHint,
  rows,
  askGroups,
  hiddenRows,
  refMaps,
  hasMore,
  hasNewer,
  atBottom,
  loadingHistory,
  loadingOlder,
  openAt,
  onTimelineScroll,
  scrollRef,
  contentRef,
  dayLabels,
  unreadAnchorId,
  runEdges,
  outboxEdge,
  pendingBlock,
  outgoingState,
  retrySend,
  outbox,
  typingRows,
  clearReply,
  noticeAgent,
  parentOf,
  showReplyCue,
  fmtTime,
  isMine,
  displayName,
  authorTitle,
  isExternal,
  avatarSrc,
  myName,
  mentionPool,
  onMessagesClick,
  onTimelinePointer,
  hideBar,
  togglePicker,
  bar,
  barBlock,
  kbdNav,
  reactionPickerFor,
  timeShownId,
  arrived,
  sentNow,
  delivered,
  flashId,
  settleArrival,
  settleSent,
  outboxLeave,
  jumpToUnseen,
  unseen,
  sheet,
  sheetBlock,
  draft,
  draftQuote,
  clearDraftQuote,
  composerRef,
  starterPrompts,
  showStarters,
  startDraft,
  pendingAtts,
  attsUploading,
  addFiles,
  addLibraryFile,
  onComposerPaste,
  onComposerDrop,
  removePendingAtt,
  retryPendingAtt,
  replyLabel,
  editingId,
  editSaving,
  canEdit,
  startEdit,
  saveEdit,
  editSend,
  canRetryAt,
  retryBusy,
  retryNow,
  onComposerSend,
  hearVoice,
  errorMsg,
  linkDown,
  send,
  replyToQuestion,
  viewer,
  postChecklist,
  changeChecklist,
  onReact,
  setReply,
  downloadAttachment,
  isAgentBlock,
  AUTHOR,
} = panel

// 撤回一道题上的回答。它要的两样（时间线、错误横幅）都在 `panel` 上，但它自己不在
// `useChatPanel` 里：那个文件贴着行数上限，一行都长不了。
const { withdrawQuestionAnswer, skipQuestion } = useAskResponse({ timeline: panel.timeline, errorMsg })
// 整组提交：请求在这一层发，卡片只往上发意图（组件不取数据）。
const { submit: submitAskGroup, busy: askGroupBusy } = useAskGroupAnswer({ timeline: panel.timeline, errorMsg })

// 支线：历史读完、输入框还空着、我上一句叫过 AI 队友，就先带上 @ 它的那枚标签。只在
// 打开的那一刻做一次，删掉了不会再长回来。
watch(loadingHistory, (loading, was) => {
  if (!props.inThread || props.alwaysSummon || loading || !was || draft.value) return
  const seat = agentSeat.value
  draft.value = summonPrefill(panel.timeline.messages.value, isMine, seat ? `<@${seat.handle}>` : `@${agentName.value}`)
})

// 输入框旁边那颗队友头像要的两样：正在这一栏里跑一轮的队友，和我上一次交给的那位
// （平时画它，点一下就交给它）。后者从我自己发过的话里找：最近一条 @ 了队友的那条。
const busyAgents = computed(() =>
  Object.entries(agentFaces.value)
    .filter(([, face]) => face.status !== null)
    .map(([handle]) => handle)
)
const lastMate = computed(() => {
  const messages = panel.timeline.messages.value
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.kind !== 'message' || !isMine(m)) continue
    for (const [, handle] of (m.content ?? '').matchAll(/<@([\w-]+)>/g)) if (isAgentHandle(handle)) return handle
  }
  return null
})

// The timeline binds these two with `:ref`, so it has to receive the Refs
// themselves — a template binding would unwrap them into elements. Passing them
// inside a plain object keeps them intact: props are shallow, not deep.
const timelineRefs = { scrollRef, contentRef }

// 新手引导要的事实（交给过芝士没有、采纳过没有……）：引导走完了就不再问。没开始时只在项目
// 本体那一栏问（引导从那里开始）；开始以后哪一栏都问，任务页上的那几步也要知道人走到哪了。
// 房间里每来一条消息再问一次。
const guide = useProjectTour()
const startSteps = useStartSteps(
  () => (!guide.done.value && (onProjectLine.value || guide.active.value) ? props.topic?.project_id : null),
  () => rows.value.length
)
// 新手引导画在应用最外层（App.vue），任务页上也要出现；做到哪一步看这几条事实。
watch(startSteps, (steps) => steps && noteFacts(steps), { immediate: true })
const retryIndex = computed(() => (canRetryAt(rows.value.length - 1) ? rows.value.length - 1 : -1))
const barEditable = computed(() => !!barBlock.value && canEdit(barBlock.value))

// The page that owns the address drives the composer through this (TopicView
// keeps its own input bar for the root topic), so it stays exposed.
const submitQuestion = createQuestionSubmit({
  topic: () => props.topic,
  agentSeat: () => agentSeat.value,
  mentionPool: () => mentionPool.value,
  topicList: () => props.topicList,
  send,
})
defineExpose({ send, linkDown, submitQuestion })

// 看图浮层要的三样（lib/chatImages）：这段对话里有哪些图、右侧面板、输入框，都在这一层。
provide(CHAT_IMAGE_ACTIONS, {
  gallery: () => [
    ...new Set(
      rows.value
        .flatMap((row) => (row.run.length ? row.run : [row.block]))
        .filter(isImageBlock)
        .map((b) => b.content)
    ),
  ],
  openInPanel: (path, tool) => {
    handOffImageTool(path, tool)
    emit('open-file', path)
  },
  attach: (file) => void addFiles([file]),
})

// 一个键从房间里任何地方回到这一栏的输入框（见 useComposerShortcut）。
useComposerShortcut({
  root: chatRoot,
  open: () => !!props.topic && props.showComposer && !props.composerClosed && !!composerRef.value,
  focus: () => composerRef.value?.focus(),
})
</script>

<template>
  <!-- The layout lives in `.chat` below, NOT in Vuetify's d-flex/flex-column/
       fill-height utilities. Those carry `!important`, and 专注模式 hides this
       whole panel with `v-show` — which sets inline `display: none`, which
       `.d-flex { display: flex !important }` then overrides. The button
       toggled, the icon flipped, and the chat column never moved. -->
  <div ref="chatRoot" class="chat">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-forum-outline</v-icon>
        <div>{{ t('work.room.chat.pickTopic') }}</div>
      </div>
    </div>
    <template v-else>
      <ChatPanelHeader
        :topic="topic"
        :connected="!linkDown"
        :pr-header="prHeader"
        :hide-header="hideHeader"
        :pr-short-id="prShortId"
        :pr-state="prState"
        :title-override="titleOverride"
        :back-label="backLabel"
        @back="emit('back')"
      >
        <template v-if="$slots['header-actions']" #actions><slot name="header-actions" /></template>
      </ChatPanelHeader>

      <!-- The timeline gets its own boundary: one bad row must not take the
           composer, the new-message pill or the error toast down with it.
           resetKey = topic id, so switching topics recovers on its own. -->
      <div class="timeline-zone">
        <ErrorBoundary :reset-key="topic.id">
          <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events -- the keyboard twins are the focusin/focusout listeners useChatRowActions puts on the same scroller -->
          <ChatTimeline
            v-guide-anchor="onProjectLine ? 'timeline' : ''"
            :topic="topic"
            :ask-closed="askClosed"
            :no-upgrade="noUpgrade"
            :threadable="threadable"
            :processable="inThread"
            :fold-plain="!!conversationId"
            :replying-for="threadLines.replyingFor"
            :replying-seats-for="threadLines.replyingSeats"
            :thread-status-for="threadLines.statusFor"
            :pinnable="pinnable"
            :pinned-ids="pins.pinnedIds.value"
            :chapterable="chapterable"
            :chapter-ids="chapterIds"
            :chapters="chapters"
            :task-of="taskOf"
            :tasks-under="tasksUnder"
            :name-of="nameOf"
            :rows="rows"
            :ask-groups="askGroups"
            :ask-group-busy="askGroupBusy"
            :ask-group-failure="errorMsg"
            :hidden-rows="hiddenRows"
            :day-labels="dayLabels"
            :unread-anchor-id="unreadAnchorId"
            :run-edges="runEdges"
            :arrived="arrived"
            :delivered="delivered"
            :sent-now="sentNow"
            :flash-id="flashId"
            :time-shown-id="timeShownId"
            :bar="bar"
            :bar-block="barBlock"
            :bar-editable="barEditable"
            :kbd-nav="kbdNav"
            :reaction-picker-for="reactionPickerFor"
            :loading-history="loadingHistory"
            :has-more="hasMore"
            :loading-older="loadingOlder"
            :retry-index="retryIndex"
            :retry-busy="retryBusy"
            :working="awaitingReply"
            :show-starters="showStarters"
            :starter-prompts="starterPrompts"
            :agent-name="agentName"
            :refs="refMaps"
            :outbox="outbox"
            :typing="typingRows"
            :editing-id="editingId"
            :edit-saving="editSaving"
            :mention-pool="mentionPool"
            :topic-list="topicList"
            :scroll-ref="timelineRefs.scrollRef"
            :content-ref="timelineRefs.contentRef"
            :is-agent-block="isAgentBlock"
            :is-mine="isMine"
            :is-external="isExternal"
            :avatar-of="avatarSrc"
            :display-name="displayName"
            :author-title="authorTitle"
            :notice-agent="noticeAgent"
            :agent-faces="agentFaces"
            :parent-of="parentOf"
            :show-reply-cue="showReplyCue"
            :fmt-time="fmtTime"
            :pending-block="pendingBlock"
            :outgoing-state="outgoingState"
            :outbox-edge="outboxEdge"
            :my-name="myName"
            :viewer="viewer"
            @scroll="onTimelineScroll"
            @click="onMessagesClick"
            @mouseover="onTimelinePointer"
            @mouseleave="hideBar"
            @react="onReact"
            @reply="setReply"
            @open-thread="emit('open-thread', $event)"
            @open-process="emit('open-process', $event)"
            @upgrade-message="emit('upgrade-message', $event)"
            @edit="startEdit"
            @edit-send="editSend"
            @toggle-picker="togglePicker"
            @open-file="(path) => emit('open-file', path)"
            @open-card="emit('open-card', $event)"
            @open-resource="
              (resource, turnId, review, document) => emit('open-resource', resource, turnId, review, document)
            "
            @ask-reply="replyToQuestion"
            @ask-withdraw="withdrawQuestionAnswer"
            @ask-skip="skipQuestion"
            @ask-group-submit="submitAskGroup"
            @checklist="changeChecklist"
            @download="downloadAttachment"
            @jump="openAt"
            @save-edit="saveEdit"
            @cancel-edit="editingId = null"
            @retry="retryNow"
            @retry-send="retrySend"
            @starter="startDraft"
            @settle-arrival="settleArrival"
            @settle-sent="settleSent"
            @outbox-leave="outboxLeave"
            @pin="pins.pin"
            @unpin="pins.unpin"
            @chapter="emit('chapter', $event)"
            @unchapter="emit('unchapter', $event)"
            @keep="keepFile"
          >
            <template #timeline-start><slot name="timeline-start" /></template>
            <template #timeline-end><slot name="timeline-end" /></template>
          </ChatTimeline>
        </ErrorBoundary>
        <!-- Floating bookmark rail at the top-left of the timeline; hidden when empty. -->
        <BookmarkRail
          :chapters="chapters"
          :can-chapter="chapterable"
          :refs="refMaps"
          :active-id="flashId"
          @jump="openAt"
          @unpin="emit('unchapter', $event)"
          @rename="(id: string, title: string) => emit('rename', id, title)"
        />
      </div>

      <RoomMessageSheet
        v-model="sheet.open"
        :block="sheetBlock"
        :is-agent="!!sheetBlock && isAgentBlock(sheetBlock)"
        :editable="!!sheetBlock && canEdit(sheetBlock)"
        :no-upgrade="noUpgrade"
        :threadable="threadable"
        :processable="inThread"
        :pinnable="pinnable"
        :pinned-ids="pins.pinnedIds.value"
        :chapterable="chapterable"
        :chaptered-ids="chapterIds"
        @react="onReact"
        @reply="setReply"
        @thread="emit('open-thread', $event)"
        @process="emit('open-process', $event)"
        @upgrade="emit('upgrade-message', $event)"
        @edit="startEdit"
        @pin="pins.pin"
        @unpin="pins.unpin"
        @chapter="emit('chapter', $event)"
        @unchapter="emit('unchapter', $event)"
      />
      <ChatNewMessagesPill :count="unseen.length" :has-newer="hasNewer" :at-bottom="atBottom" @jump="jumpToUnseen" />

      <ChatErrorToast :message="errorMsg" @close="errorMsg = null" />

      <!-- 贴在输入框上方的那一条（验收卡）。它不随对话滚：等人做的决定要一直看得见，
           又不该每来一条消息就被推走、或者反过来把对话挤到只剩几行。 -->
      <slot name="above-composer" />

      <!-- 输入框那一块。「谁在忙」在它上面单占一格，不压在对话上：时间线底部让出这一
           格，输入框自己不动。没人在忙时这一格不存在，对话区就长回去。 -->
      <div class="composer-zone">
        <MemberActivity v-if="showComposer" class="composer-activity" :lines="activityLines" />
        <div v-if="showComposer && composerClosed" class="composer-closed">
          <div class="composer-closed__box t-body">
            <span class="composer-closed__text">{{ composerClosed }}</span>
            <slot name="composer-closed" />
          </div>
        </div>
        <!-- Built-in composer (private chat / standalone use). A question 芝士 asked
             never takes its place: its quick replies sit under the question. -->
        <RoomComposer
          v-else-if="showComposer"
          ref="composerRef"
          v-model="draft"
          v-guide-anchor="inThread ? 'thread-composer' : ''"
          :guided="onProjectLine"
          :topic="topic"
          :mention-pool="mentionPool"
          :topic-list="topicList"
          :refs="refMaps"
          :agent-seat="agentSeat"
          :agent-name="agentName"
          :always-summon="alwaysSummon"
          :hand-off="handOff"
          :hint="hint ?? (inThread ? t('work.room.thread.placeholder') : composerHint)"
          :atts="pendingAtts"
          :atts-uploading="attsUploading"
          :reply-label="replyLabel"
          :quote="draftQuote"
          :post-checklist="postChecklist"
          :hear-voice="hearVoice"
          :busy-agents="busyAgents"
          :last-mate="lastMate"
          @send="onComposerSend"
          @clear-reply="clearReply"
          @clear-quote="clearDraftQuote"
          @files="(files) => void addFiles(files)"
          @drop-files="onComposerDrop"
          @paste="onComposerPaste"
          @remove-att="removePendingAtt"
          @retry-att="(i: number) => void retryPendingAtt(i)"
          @add-library-file="(path) => void addLibraryFile(path)"
        >
          <template #composer-chips><slot name="composer-chips" /></template>
          <template v-if="outsidePrompt" #strip>
            <MentionOutsidePrompt
              docked
              :names="mentionOutside.names(outsidePrompt.handles)"
              :members-only="outsidePrompt.members_only"
              :may-add="outsidePrompt.may_add"
              :adding="outsideAdding"
              @add="mentionOutside.add"
              @dismiss="mentionOutside.dismiss"
            />
          </template>
        </RoomComposer>
      </div>
    </template>
    <KeepToLibraryConfirm
      :name="keeping ? keeping.content.split('/').pop() ?? keeping.content : null"
      :people="keepAudience.people.value"
      :project-people="keepAudience.projectPeople.value"
      @confirm="confirmKeep"
      @cancel="keeping = null"
    />
  </div>
</template>

<style scoped>
/* 不能说话时，输入框的位置留着同样大小的一格，写着为什么：和输入框同一个圆角描边，
   底色退一档，读得出这里平时是输入框。 */
.composer-closed {
  width: 100%;
  max-width: var(--page-w-read);
  margin-inline: auto;
  padding: 8px 12px;
}
@media (max-width: 959.98px) {
  .composer-closed {
    max-width: var(--page-w);
  }
}
.composer-closed__box {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 46px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--fill);
  color: var(--muted);
}
.composer-closed__text {
  min-width: 0;
}
.chat {
  /* Was `d-flex flex-column fill-height` on the root. Spelled here instead so
     the declarations carry normal specificity: v-show's inline `display: none`
     has to be able to win. See the comment on the root element. */
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}
/* 时间线的那一块：书签条要浮在它上面，而不是跟着消息滚走。 */
.timeline-zone {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
/* 手机底部的安全区（Home 横杠 / 圆角）由输入框这一块的最后一样东西出：输入区，
   或者说明为什么不能说话的那一格。 */
.chat .composer,
.chat .composer-closed {
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
}
.composer-zone {
  position: relative;
  flex: none;
  display: flex;
  flex-direction: column;
}
/* 在输入框上面单占一格（`.composer-zone` 是 flex 列，它在输入框前面）：时间线的可
   视高度跟着少这一格，最后一条消息不会被压住。几位队友在忙就占几行，没人在忙时组件
   本身不渲染，这一格也就不存在。 */
.chat .composer-activity {
  padding: 10px 16px 2px;
  font-size: 12px;
  line-height: var(--lh-12);
}
/* 输入框和它上面那行状态收成和对话同一栏（时间线那一份在 ChatTimeline）：桌面上
   是读的一栏 --page-w-read，手机外壳里是 --page-w。三块（时间线、输入框、贴在上
   面的那一条）用同一个值，栏才对齐。 */
.composer,
.composer-activity {
  width: 100%;
  max-width: var(--page-w-read);
  margin-inline: auto;
}
@media (max-width: 959.98px) {
  .composer,
  .composer-activity {
    max-width: var(--page-w);
  }
}
</style>
