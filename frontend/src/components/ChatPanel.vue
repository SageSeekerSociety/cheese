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
import type { AskGroupAction } from '../lib/askGroupState'

import { computed } from 'vue'

import { type ChatPanelEmit, useChatPanel } from '../composables/useChatPanel'
import { createQuestionSubmit } from '../lib/previewQuestion'

import AskGroupFlow from './ask/AskGroupFlow.vue'
import ChatErrorToast from './chat/ChatErrorToast.vue'
import ChatNewMessagesPill from './chat/ChatNewMessagesPill.vue'
import ChatPanelHeader from './chat/ChatPanelHeader.vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import ErrorBoundary from './common/ErrorBoundary.vue'
import MemberActivity from './room/MemberActivity.vue'
import MessageQuote from './room/MessageQuote.vue'
import RoomComposer from './room/RoomComposer.vue'
import RoomMessageSheet from './room/RoomMessageSheet.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
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
  refMaps,
  hasMore,
  hasNewer,
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
  startDraft,
  pendingAtts,
  attsUploading,
  addFiles,
  addLibraryFile,
  onComposerPaste,
  onComposerDrop,
  removePendingAtt,
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
  askGroupAction,
  askStates,
  askAction,
  askViewer,
  askTakeover,
  askReturn,
  dismissAsk,
  restoreAsk,
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

// Per-row questions the view asks. The panel hands over data; these are read
// off it once for the one row that needs them, not once per render.
const retryIndex = computed(() => (canRetryAt(rows.value.length - 1) ? rows.value.length - 1 : -1))
const barEditable = computed(() => !!barBlock.value && canEdit(barBlock.value))

// 接管时面板的动作要走回那一组。接管组是响应式计算出来的，这里读一次当前值即可。
const takeoverScope = computed(() => askTakeover.value?.scope ?? null)
function onAskAction(action: AskGroupAction) {
  if (takeoverScope.value) askGroupAction(takeoverScope.value, action)
}
function onAskDismiss() {
  if (takeoverScope.value) dismissAsk(takeoverScope.value)
}

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
          :rows="rows"
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
          :ask-states="askStates"
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
          :viewer="askViewer"
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
          @open-file="(path, taskId) => emit('open-file', path, taskId)"
          @open-topic="emit('open-topic', $event)"
          @open-card="emit('open-card', $event)"
          @open-resource="(resource, turnId, review) => emit('open-resource', resource, turnId, review)"
          @ask-action="askAction"
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
        @react="onReact"
        @reply="setReply"
        @upgrade="emit('upgrade-message', $event)"
        @edit="startEdit"
      />
      <ChatNewMessagesPill :count="unseen.length" :has-newer="hasNewer" @jump="jumpToUnseen" />

      <ChatErrorToast :message="errorMsg" @close="errorMsg = null" />

      <!-- 贴在输入框上方的那一条（验收卡）。它不随对话滚：等人做的决定要一直看得见，
           又不该每来一条消息就被推走、或者反过来把对话挤到只剩几行。 -->
      <slot name="above-composer" />

      <div v-if="showComposer && draftQuote" class="composer-quote">
        <MessageQuote :quote="draftQuote" />
        <button type="button" class="composer-quote__remove" @click="clearDraftQuote">
          {{ t('slides.removeQuote') }}
        </button>
      </div>
      <!-- 提问接管输入框：两者是同一格里的二选一，不是浮层。有题要答就把
           composer 换下来（不用点），答完或没有题时它自己回来。Esc 收起后
           「有 N 个问题待回答」那一条让人重新把它叫回来，问题不会被永久藏掉。 -->
      <button v-if="showComposer && askReturn > 0" type="button" class="composer-ask-return" @click="restoreAsk">
        {{ t('ask.group.returnHint', { count: askReturn }) }}
      </button>
      <AskGroupFlow
        v-if="showComposer && askTakeover"
        :state="askTakeover"
        :viewer="askViewer"
        :names="refMaps.mentionNames"
        composer
        @action="onAskAction"
        @dismiss="onAskDismiss"
      />
      <!-- Built-in composer (private chat / standalone use). -->
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
.composer-quote {
  margin: 0 16px 8px;
}
.composer-ask-return {
  align-self: center;
  padding: 4px 12px;
  margin: 0 16px 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
}
.composer-ask-return:hover {
  color: var(--text);
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
/* 手机外壳里输入框收成和对话同一栏（时间线那一份在 ChatTimeline）。 */
@media (max-width: 959.98px) {
  .composer,
  .composer-activity {
    width: 100%;
    max-width: var(--page-w);
    margin-inline: auto;
  }
}
</style>
