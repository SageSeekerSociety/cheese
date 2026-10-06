<script setup lang="ts">
// The room's chat column. It is a shell now: props in, one composable
// (useChatPanel) for everything the room is doing, and markup that draws.
//
// It used to be 1987 lines holding the socket, the history window, the
// composer, the row animations and the whole timeline at once. The seam is the
// one RoomMessage already draws around a row — a composable knows what the room
// is doing, a view knows what it looks like — and the views under ./chat are
// those pieces: header, timeline, new-message pill, error toast.
import type { ProjectMemberRow, Topic } from '../cx_types'

import { computed } from 'vue'

import { type ChatPanelEmit, useChatPanel } from '../composables/useChatPanel'
import { useGettingStarted } from '../composables/useGettingStarted'
import { createQuestionSubmit } from '../lib/previewQuestion'

import ChatErrorToast from './chat/ChatErrorToast.vue'
import ChatNewMessagesPill from './chat/ChatNewMessagesPill.vue'
import ChatPanelHeader from './chat/ChatPanelHeader.vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import ErrorBoundary from './common/ErrorBoundary.vue'
import GettingStartedCard from './room/GettingStartedCard.vue'
import MemberActivity from './room/MemberActivity.vue'
import MessageQuote from './room/MessageQuote.vue'
import RoomComposer from './room/RoomComposer.vue'
import RoomMessageSheet from './room/RoomMessageSheet.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 私聊里的消息不能转为任务：不给「转为任务」。 */
    noUpgrade?: boolean
    // 这一栏里每条消息都是说给芝士听的：1:1 私聊那种只有它一个对话方的地方。
    // 别处叫它靠 @ 它（和 @ 人同一套），见 sendDraft。
    alwaysSummon?: boolean
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
    // 这里此刻不能说话，以及为什么（任务只有负责人能说话、任务已关闭）。输入框的
    // 位置换成这一句，`composer-closed` 插槽接在它后面。
    composerClosed?: string | null
  }>(),
  {
    alwaysSummon: false,
    showComposer: false,
    prHeader: false,
    hideHeader: false,
    members: () => [],
    topicList: () => [],
    titleOverride: null,
    backLabel: null,
    unreadOnOpen: 0,
    conversationId: null,
    composerClosed: null,
  }
)

// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). The list itself — and what each
// event means — is ChatPanelEmit in ../composables/useChatPanel: the panel and
// whoever listens to it have to agree, so there is one declaration, not two.
const emit = defineEmits<ChatPanelEmit>()

// A composable is not re-run when a prop changes: it reads the current value
// when it needs it. That is why every prop is handed over as an accessor.
const panel = useChatPanel({
  topic: () => props.topic,
  conversationId: () => props.conversationId,
  alwaysSummon: () => props.alwaysSummon,
  showComposer: () => props.showComposer,
  members: () => props.members,
  topicList: () => props.topicList,
  unreadOnOpen: () => props.unreadOnOpen,
  focusBlock: () => props.focusBlock ?? null,
  emit,
})

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
  splitMarkers,
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
  // 「开始清单」的两条房间内判据（其余两条问服务端和名册）。
  agentHasSpoken,
  roomHasAttachment,
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
  errorMsg,
  connected,
  send,
  replyToQuestion,
  viewer,
  postChecklist,
  changeChecklist,
  onReact,
  setReply,
  undoTitle,
  downloadAttachment,
  onAvatarError,
  isAgentBlock,
  AUTHOR,
} = panel

// The timeline binds these two with `:ref`, so it has to receive the Refs
// themselves — a template binding would unwrap them into elements. Passing them
// inside a plain object keeps them intact: props are shallow, not deep.
const timelineRefs = { scrollRef, contentRef }

// 「开始清单」：只画在项目本体（root 话题）上。判据和退休规则都在这个 composable
// 里，这里只把手的四个来源交给它。
const {
  visible: showGettingStarted,
  steps: gettingStartedSteps,
  dismiss: dismissGettingStarted,
} = useGettingStarted({
  projectId: () => props.topic?.project_id ?? null,
  on: () => props.topic?.kind === 'root' && props.topic.status !== 'archived',
  agentHasSpoken: () => agentHasSpoken.value,
  roomHasAttachment: () => roomHasAttachment.value,
  members: () => props.members,
})

// Per-row questions the view asks. The panel hands over data; these are read
// off it once for the one row that needs them, not once per render.
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
defineExpose({ send, connected, submitQuestion })
</script>

<template>
  <!-- The layout lives in `.chat` below, NOT in Vuetify's d-flex/flex-column/
       fill-height utilities. Those carry `!important`, and 专注模式 hides this
       whole panel with `v-show` — which sets inline `display: none`, which
       `.d-flex { display: flex !important }` then overrides. The button
       toggled, the icon flipped, and the chat column never moved. -->
  <div class="chat">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-forum-outline</v-icon>
        <div>{{ t('work.room.chat.pickTopic') }}</div>
      </div>
    </div>
    <template v-else>
      <ChatPanelHeader
        :topic="topic"
        :connected="connected"
        :pr-header="prHeader"
        :hide-header="hideHeader"
        :pr-short-id="prShortId"
        :pr-state="prState"
        :title-override="titleOverride"
        :back-label="backLabel"
        @back="emit('back')"
      />

      <!-- The timeline gets its own boundary: one bad row must not take the
           composer, the new-message pill or the error toast down with it.
           resetKey = topic id, so switching topics recovers on its own. -->
      <ErrorBoundary :reset-key="topic.id">
        <ChatTimeline
          :topic="topic"
          :no-upgrade="noUpgrade"
          :rows="rows"
          :hidden-rows="hiddenRows"
          :day-labels="dayLabels"
          :unread-anchor-id="unreadAnchorId"
          :split-markers="splitMarkers"
          :run-edges="runEdges"
          :arrived="arrived"
          :delivered="delivered"
          :sent-now="sentNow"
          :flash-id="flashId"
          :time-shown-id="timeShownId"
          :bar="bar"
          :bar-block="barBlock"
          :bar-editable="barEditable"
          :reaction-picker-for="reactionPickerFor"
          :loading-history="loadingHistory"
          :has-more="hasMore"
          :loading-older="loadingOlder"
          :retry-index="retryIndex"
          :retry-busy="retryBusy"
          :working="awaitingReply"
          :show-starters="showStarters"
          :starter-prompts="starterPrompts"
          :agent-seat="agentSeat ?? undefined"
          :agent-name="agentName"
          :refs="refMaps"
          :outbox="outbox"
          :typing="typingRows"
          :editing-id="editingId"
          :edit-saving="editSaving"
          :scroll-ref="timelineRefs.scrollRef"
          :content-ref="timelineRefs.contentRef"
          :is-agent-block="isAgentBlock"
          :is-mine="isMine"
          :is-external="isExternal"
          :avatar-src="avatarSrc"
          :display-name="displayName"
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
          @upgrade-message="emit('upgrade-message', $event)"
          @edit="startEdit"
          @edit-send="editSend"
          @toggle-picker="togglePicker"
          @open-file="(path) => emit('open-file', path)"
          @open-topic="emit('open-topic', $event)"
          @open-card="emit('open-card', $event)"
          @open-resource="
            (resource, turnId, review, document) => emit('open-resource', resource, turnId, review, document)
          "
          @ask-reply="replyToQuestion"
          @checklist="changeChecklist"
          @download="downloadAttachment"
          @jump="openAt"
          @avatar-error="onAvatarError"
          @save-edit="saveEdit"
          @cancel-edit="editingId = null"
          @retry="retryNow"
          @retry-send="retrySend"
          @undo-title="undoTitle"
          @starter="startDraft"
          @settle-arrival="settleArrival"
          @settle-sent="settleSent"
          @outbox-leave="outboxLeave"
        >
          <template #timeline-end><slot name="timeline-end" /></template>
        </ChatTimeline>
      </ErrorBoundary>

      <RoomMessageSheet
        v-model="sheet.open"
        :block="sheetBlock"
        :is-agent="!!sheetBlock && isAgentBlock(sheetBlock)"
        :editable="!!sheetBlock && canEdit(sheetBlock)"
        :no-upgrade="noUpgrade"
        @react="onReact"
        @reply="setReply"
        @upgrade="emit('upgrade-message', $event)"
        @edit="startEdit"
      />
      <ChatNewMessagesPill :count="unseen.length" :has-newer="hasNewer" :at-bottom="atBottom" @jump="jumpToUnseen" />

      <ChatErrorToast :message="errorMsg" @close="errorMsg = null" />

      <!-- 开始清单：和验收卡同一格，理由也一样——它是一串等人做的下一步，贴在这里
           才一直看得见。四步做掉两步（说上话、放进材料）它就自己退场，不用人收。 -->
      <GettingStartedCard
        v-if="showGettingStarted && topic.project_id"
        :steps="gettingStartedSteps"
        :project-id="topic.project_id"
        :agent-name="agentName"
        @dismiss="dismissGettingStarted"
      />

      <!-- 贴在输入框上方的那一条（验收卡）。它不随对话滚：等人做的决定要一直看得见，
           又不该每来一条消息就被推走、或者反过来把对话挤到只剩几行。 -->
      <slot name="above-composer" />

      <div v-if="showComposer && !composerClosed && draftQuote" class="composer-quote">
        <MessageQuote :quote="draftQuote" />
        <button type="button" class="composer-quote__remove" @click="clearDraftQuote">
          {{ t('slides.removeQuote') }}
        </button>
      </div>
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
        :topic="topic"
        :mention-pool="mentionPool"
        :topic-list="topicList"
        :agent-seat="agentSeat"
        :agent-name="agentName"
        :always-summon="alwaysSummon"
        :hint="composerHint"
        :atts="pendingAtts"
        :atts-uploading="attsUploading"
        :reply-label="replyLabel"
        :post-checklist="postChecklist"
        @send="onComposerSend"
        @clear-reply="clearReply"
        @files="(files) => void addFiles(files)"
        @drop-files="onComposerDrop"
        @paste="onComposerPaste"
        @remove-att="removePendingAtt"
        @retry-att="(i: number) => void retryPendingAtt(i)"
        @add-library-file="(path) => void addLibraryFile(path)"
      >
        <template #composer-chips><slot name="composer-chips" /></template>
      </RoomComposer>
      <!-- 谁在这个房间里忙，和 Slack 一样贴在输入框正下方：「Alice 正在输入…」
           「Cedar 正在工作…」。说的是成员，不是房间。 -->
      <MemberActivity v-if="showComposer" class="composer-activity" :lines="activityLines" reserve />
    </template>
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
.composer-quote {
  margin: 0 16px 8px;
}
.composer-quote__remove {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  padding: 4px 0;
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
/* 这一列最后一行永远是「谁在工作」那一行（MemberActivity，showComposer 时一直画着，
   用 reserve 占住高度）。手机底部的安全区（Home 横杠 / 圆角）由它一个人出：它上面
   的输入区把自己那份让掉。两边各留一份的话，横杠上方会叠出两倍的空。 */
.chat .composer {
  padding-bottom: 8px;
}
.chat .composer-activity {
  padding-bottom: calc(4px + env(safe-area-inset-bottom));
}
/* 输入框和它下面那行状态收成和对话同一栏（时间线那一份在 ChatTimeline）：桌面上
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
