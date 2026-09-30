<script setup lang="ts">
// The conversation itself: the scrolling pane, the rows, and the things that
// hang off it (hover bar, skeleton, starters, the older-page loader, the
// outbox, the host's timeline-end slot).
//
// Everything it draws arrives as a prop and everything a person does leaves as
// an event: this component knows what a row LOOKS like, never what the room is
// doing. The three animation sets (`arrived` / `older` / `delivered`) are read
// here as class bindings and cleared by the row's own animationend.
import type { Ref } from 'vue'
import type { Block, Topic } from '../../cx_types'
import type { RunEdge } from '../../lib/chatGrouping'
import type { Outgoing } from '../../lib/composerDrafts'
import type { NoticeAgent, NoticeRow, PlatformNotice } from '../../lib/platformNotice'
import type { SplitMarker } from '../../lib/splitMarkers'

import { editableText } from '../../lib/renderMessage'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import DispatchedMarker from '../DispatchedMarker.vue'
import RoomHoverBar from '../room/RoomHoverBar.vue'
import RoomMessage from '../room/RoomMessage.vue'
import RoomNotice from '../room/RoomNotice.vue'
import TimelineMark from '../TimelineMark.vue'

import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

defineProps<{
  topic: Topic | null
  rows: NoticeRow[]
  dayLabels: Map<string, string>
  unreadAnchorId: string | null
  splitMarkers: { before: Map<string, SplitMarker[]>; tail: SplitMarker[] }
  runEdges: RunEdge[]
  arrived: Set<string>
  older: Set<string>
  delivered: Set<string>
  sentNow: Set<string>
  flashId: string | null
  timeShownId: string | null
  bar: { id: string | null; shown: boolean; top: number; jump: boolean }
  barBlock: Block | null
  barEditable: boolean
  reactionPickerFor: string | null
  loadingHistory: boolean
  hasMore: boolean
  loadingOlder: boolean
  /** Index of the one row the retry button may sit on (the last one), or -1. */
  retryIndex: number
  retryBusy: boolean
  showStarters: boolean
  starterPrompts: { label: string; text: string }[]
  agentSeat: { handle?: string } | undefined
  agentName: string
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
  outbox: Outgoing[]
  editingId: string | null
  editSaving: boolean
  askBusy: string | null
  /** Bound with `:ref`, so the pane the panel measures is this one. */
  scrollRef: Ref<HTMLElement | null>
  contentRef: Ref<HTMLElement | null>
  /* Renderers that belong to the room, not to a row: names, avatars, times. */
  isAgentBlock: (b: Block) => boolean
  isMine: (m: Block) => boolean
  isExternal: (handle: string) => boolean
  avatarSrc: (handle: string) => string | null
  displayName: (m: Block) => string
  noticeAgent: (m: Block, notice: PlatformNotice) => NoticeAgent | null
  parentOf: (m: Block) => Block | undefined
  showReplyCue: (m: Block) => boolean
  fmtTime: (iso: string) => string
  pendingBlock: (item: Outgoing) => Block
  outgoingState: (item: Outgoing) => string
  outboxEdge: (index: number) => RunEdge
  myName: string
  viewer: string
}>()

const emit = defineEmits<{
  (e: 'react', block: Block, emoji: string): void
  (e: 'reply', block: Block): void
  (e: 'upgrade-message', messageId: string): void
  (e: 'edit', block: Block): void
  (e: 'edit-send', item: Outgoing): void
  (e: 'toggle-picker', blockId: string): void
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string): void
  (e: 'open-resource', resource: string, turnId?: string): void
  (e: 'answer', block: Block, option: string): void
  (e: 'download', block: Block): void
  (e: 'jump', blockId: string): void
  (e: 'avatar-error', handle: string): void
  (e: 'save-edit', block: Block, text: string): void
  (e: 'cancel-edit'): void
  (e: 'retry'): void
  (e: 'retry-send', clientId: string): void
  (e: 'undo-title', blockId: string): void
  (e: 'starter', text: string): void
  (e: 'settle-arrival', event: AnimationEvent, id: string): void
  (e: 'settle-sent', event: AnimationEvent, clientId: string): void
  (e: 'outbox-leave', el: Element, done: () => void): void
}>()

// Child rows emit the same events the panel listens for; the extra hop is what
// keeps this component free of the room's own bookkeeping. Thin wrappers so the
// template stays a table of rows instead of a wall of arrows.
function emitReact(block: Block, emoji: string) {
  emit('react', block, emoji)
}
function emitAnswer(block: Block, option: string) {
  emit('answer', block, option)
}
function emitOpenFile(path: string, taskId: string | null) {
  emit('open-file', path, taskId)
}
function emitOpenResource(resource: string, turnId?: string) {
  emit('open-resource', resource, turnId)
}
function emitSaveEdit(block: Block, text: string) {
  emit('save-edit', block, text)
}
function emitOutboxLeave(el: Element, done: () => void) {
  emit('outbox-leave', el, done)
}
</script>

<template>
  <!-- Message stream — Feishu group chat: left-aligned rows, grouped runs,
           per-row hover action bar, centered system/event lines. -->
  <div :ref="scrollRef" class="messages flex-grow-1 overflow-y-auto py-2" data-testid="chat-scroll">
    <!-- Single wrapper so a ResizeObserver can watch the timeline's total
             content height (rows + streaming bubble + timeline-end slot). -->
    <div :ref="contentRef" class="tl-content">
      <RoomHoverBar
        :block="barBlock"
        :shown="bar.shown"
        :top="bar.top"
        :jump="bar.jump"
        :is-agent="!!barBlock && isAgentBlock(barBlock)"
        :picker-open="!!barBlock && reactionPickerFor === barBlock.id"
        :editable="barEditable"
        @react="emitReact"
        @toggle-picker="emit('toggle-picker', $event)"
        @reply="emit('reply', $event)"
        @upgrade="emit('upgrade-message', $event)"
        @edit="emit('edit', $event)"
      />
      <!-- 骨架和真的那几行同形同高：到货时骨架淡出，不推动下面的东西。 -->
      <Transition name="tl-skel">
        <LoadingSkeleton v-if="loadingHistory" variant="chat" />
      </Transition>

      <section v-if="showStarters" class="chat-start px-5 py-8" :aria-label="t('work.room.chat.startAria')">
        <h2 class="t-title mb-2">{{ t('work.room.chat.startTitle') }}</h2>
        <i18n-t scope="global" keypath="work.room.chat.startBody" tag="p" class="t-body c-muted mb-4">
          <template #agent><UserRef :handle="agentSeat?.handle" :name="agentName" /></template>
        </i18n-t>
        <div class="d-flex flex-wrap ga-2">
          <v-btn
            v-for="prompt in starterPrompts"
            :key="prompt.label"
            variant="outlined"
            color="on-surface"
            size="small"
            @click="emit('starter', prompt.text)"
            >{{ prompt.label }}</v-btn
          >
        </div>
      </section>

      <!-- Paging back through history. The row is always rendered while
               older blocks exist so the timeline's top edge does not change
               height when a fetch starts — that height change would move the
               reader mid-scroll, which is the very thing loadOlder compensates
               for. -->
      <div
        v-if="!loadingHistory && hasMore"
        class="text-medium-emphasis text-body-2 px-4 py-2 text-center"
        data-testid="chat-older-loader"
      >
        {{ loadingOlder ? t('work.room.chat.loadingOlder') : t('work.room.chat.older') }}
      </div>

      <template v-for="({ block: m, notice, run }, i) in rows" :key="m.id">
        <!-- 时间刻度: 换天了。一个跑几周的话题里，一串 09:32 / 14:07 分不出
               哪条是今天的——这条线是唯一说得出「那是上周」的东西。 -->
        <TimelineMark v-if="dayLabels.get(m.id)" quiet>{{ dayLabels.get(m.id) }}</TimelineMark>
        <!-- 时间刻度: 你上次离开时看到哪儿。侧栏的未读角标只回答「有没有新的」,
               这条线回答「新的从哪开始」。开话题时算一次就冻住，不随新消息移动。 -->
        <TimelineMark v-if="m.id === unreadAnchorId" tone="unread">
          <v-icon size="12">mdi-arrow-down</v-icon>
          {{ t('work.room.chat.newMessagesBelow') }}
        </TimelineMark>
        <!-- 「已派出」标记 (issue #314): 拆出子话题在库里不留任何 block，所以
               这一行是按支线的 created_at 现算出来的，插在它被派出去的那个时刻
               上。它不是消息，但会像 event 一样把消息分组打断。 -->
        <DispatchedMarker
          v-for="marker in splitMarkers.before.get(m.id) ?? []"
          :key="marker.taskId"
          :marker="marker"
          @open="emit('open-card', $event)"
        />
        <RoomNotice
          v-if="notice"
          :class="{ 'tl-arrive': arrived.has(m.id), 'tl-older': older.has(m.id) }"
          :block="m"
          :notice="notice"
          :run="run"
          :agent="noticeAgent(m, notice)"
          :time="fmtTime(notice.mode === 'agent-status' ? notice.updatedAt : m.created_at)"
          :agent-name="agentName"
          :refs="refs"
          :can-retry="i === retryIndex"
          :retrying="retryBusy"
          :project-id="topic?.project_id ?? null"
          @animationend="emit('settle-arrival', $event, m.id)"
          @open-resource="emitOpenResource"
          @undo-title="emit('undo-title', $event)"
          @open-card="emit('open-card', $event)"
          @retry="emit('retry')"
        />
        <!-- message row -->
        <RoomMessage
          v-else-if="!notice"
          :class="{
            'tl-arrive': arrived.has(m.id),
            'tl-older': older.has(m.id),
            'tl-flash': flashId === m.id,
            'tl-delivered': delivered.has(m.id),
            'im-row--time': timeShownId === m.id,
          }"
          :block="m"
          :parent="showReplyCue(m) ? parentOf(m) ?? null : null"
          :parent-name="showReplyCue(m) ? displayName(parentOf(m)!) : null"
          :run-start="runEdges[i] !== 'cont'"
          :regroup="runEdges[i] === 'regroup'"
          :mine="isMine(m)"
          :topic-id="topic?.id ?? null"
          :author-name="displayName(m)"
          :external="isExternal(m.author)"
          :avatar="avatarSrc(m.author)"
          :is-agent="isAgentBlock(m)"
          :time="fmtTime(m.created_at)"
          :refs="refs"
          :viewer="viewer"
          :active="bar.shown && bar.id === m.id"
          :ask-busy="askBusy === m.id"
          :editing="editingId === m.id"
          :edit-text="editingId === m.id ? editableText(m.content, refs) : undefined"
          :saving="editSaving"
          @animationend="emit('settle-arrival', $event, m.id)"
          @open-file="emitOpenFile"
          @open-topic="emit('open-topic', $event)"
          @open-card="emit('open-card', $event)"
          @react="emitReact"
          @answer="emitAnswer"
          @download="emit('download', $event)"
          @jump="emit('jump', $event)"
          @avatar-error="emit('avatar-error', $event)"
          @save-edit="emitSaveEdit(m, $event)"
          @cancel-edit="emit('cancel-edit')"
        />
      </template>

      <!-- 比时间线上每一条消息都新的「已派出」标记 —— 刚派出去、之后房间里还
             没人说过话的那些支线。 -->
      <DispatchedMarker
        v-for="marker in splitMarkers.tail"
        :key="marker.taskId"
        :marker="marker"
        @open="emit('open-card', $event)"
      />

      <!-- 发件箱: 已经打出去、还没落库的消息。它长得就是一条自己发的消息,
             只是时间那一格写的是送达状态——「立即显示」是第一位的，送达状态是
             第二位的。 -->
      <TransitionGroup :css="false" @leave="emitOutboxLeave">
        <RoomMessage
          v-for="(item, oi) in outbox"
          :key="item.clientId"
          :class="{ 'tl-sent': sentNow.has(item.clientId) }"
          :data-cid="item.clientId"
          :block="pendingBlock(item)"
          :parent="null"
          :parent-name="null"
          :run-start="outboxEdge(oi) !== 'cont'"
          :regroup="outboxEdge(oi) === 'regroup'"
          :mine="true"
          :topic-id="topic?.id ?? null"
          :author-name="myName"
          :external="isExternal(viewer)"
          :avatar="avatarSrc(viewer)"
          :is-agent="false"
          :time="outgoingState(item)"
          :refs="refs"
          :viewer="viewer"
          :ask-busy="false"
          :outgoing="{ error: item.error, failed: item.state === 'failed' }"
          @animationend="emit('settle-sent', $event, item.clientId)"
          @retry="emit('retry-send', item.clientId)"
          @edit="emit('edit-send', item)"
          @avatar-error="emit('avatar-error', $event)"
        />
      </TransitionGroup>

      <!-- End of the conversation timeline — GitHub PR's merge box. Host fills. -->
      <div class="px-4">
        <slot name="timeline-end" />
      </div>
    </div>
  </div>
</template>

<style scoped src="../room/room-row.css"></style>

<style scoped>
.messages {
  background: var(--surface);
  /* A flex child's implicit min-height is its content — without this, a long
     timeline refuses to shrink and pushes whatever follows (error alert,
     reply bar) below the pane edge, clipped. */
  min-height: 0;
}
/* 骨架到货时淡出。离场时它脱离文档流，下面已经排好的真行不会被它推一下。 */
.tl-content {
  position: relative;
}
/* 手机外壳里对话不铺满整屏：平板竖屏上一行会排到六十多个字。时间线和输入框收成同
   一栏居中（贴在输入框上的那一条由放它进来的那一栏收，见 TopicChatColumn）；滚动的
   还是整块，手指在两边空白处照样滚得动。 */
@media (max-width: 959.98px) {
  .tl-content {
    width: 100%;
    max-width: var(--page-w);
    margin-inline: auto;
  }
}
.tl-skel-leave-active {
  position: absolute;
  inset: 0 0 auto;
  transition: opacity var(--dur-quick) var(--ease-in);
}
.tl-skel-leave-to {
  opacity: 0;
}
/* 新来的一条：淡入并从下面 4px 升到位（见 `arrived`）。只演一次——class 留着也
   不会重播，动画只在元素挂上的那一刻跑。 */
.tl-arrive {
  animation: tl-arrive var(--dur-base) var(--ease-out);
}
@keyframes tl-arrive {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
}
/* 自己刚发的一条：从输入框的方向升上来 12px（见 `sentNow`），比别人的新消息那
   4px 远——它确实是从下面那个框里上来的。 */
.tl-sent {
  animation: tl-sent var(--dur-base) var(--ease-out);
}
@keyframes tl-sent {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
}
/* 送达：发件箱那一行淡的那一档（RoomMessage 的 .im-row--pending）慢慢恢复。 */
.tl-delivered :deep(.im-text),
.tl-delivered :deep(.im-name) {
  animation: tl-delivered var(--dur-base) var(--ease-standard);
}
@keyframes tl-delivered {
  from {
    opacity: 0.62;
  }
}
/* 翻上去时拼进来的更早的一页：只淡入（见 `older`）。 */
.tl-older {
  animation: tl-older var(--dur-base) var(--ease-out);
}
@keyframes tl-older {
  from {
    opacity: 0;
  }
}
/* 跳到的那一条：底色从琥珀的浅底褪回去。它和新消息线、未读是同一族——「你要找的
   在这儿」。 */
.tl-flash {
  animation: tl-flash 1.6s var(--ease-out);
}
@keyframes tl-flash {
  from,
  25% {
    background-color: var(--accent-wash);
  }
}
</style>
