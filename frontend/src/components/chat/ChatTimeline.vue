<script setup lang="ts">
// The conversation itself: the scrolling pane, the rows, and the things that
// hang off it (hover bar, skeleton, starters, the older-page loader, the
// outbox, the host's timeline-end slot).
//
// Everything it draws arrives as a prop and everything a person does leaves as
// an event: this component knows what a row LOOKS like, never what the room is
// doing. The two animation sets (`arrived` / `delivered`) are read
// here as class bindings and cleared by the row's own animationend.
import type { Ref } from 'vue'
import type { Block, TodoItem, Topic } from '../../cx_types'
import type { AgentFace } from '../../lib/agentFace'
import type { AskGroupScope } from '../../lib/askGroup'
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'
import type { AskAction, AskFormState } from '../../lib/askPresentation'
import type { RunEdge } from '../../lib/chatGrouping'
import type { Outgoing } from '../../lib/composerDrafts'
import type { DocReviewRequest } from '../../lib/docReview'
import type { NoticeAgent, NoticeRow, PlatformNotice } from '../../lib/platformNotice'
import type { SplitMarker } from '../../lib/splitMarkers'

import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { groupKey, groupOf } from '../../lib/askGroup'
import { dayKey, REGROUP_GAP_MS } from '../../lib/chatGrouping'
import { editableText } from '../../lib/renderMessage'
import { formatSpan } from '../../lib/siteLog'
import { siteStatusLabel } from '../../lib/siteStatusLabel'
import AskGroupFlow from '../ask/AskGroupFlow.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import DispatchedMarker from '../DispatchedMarker.vue'
import RoomHoverBar from '../room/RoomHoverBar.vue'
import RoomMessage from '../room/RoomMessage.vue'
import RoomNotice from '../room/RoomNotice.vue'
import TimelineMark from '../TimelineMark.vue'

import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topic: Topic | null
  rows: NoticeRow[]
  dayLabels: Map<string, string>
  unreadAnchorId: string | null
  splitMarkers: { before: Map<string, SplitMarker[]>; tail: SplitMarker[] }
  runEdges: RunEdge[]
  arrived: Set<string>
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
  /** 房间里有队友正在跑这一轮。 */
  working: boolean
  showStarters: boolean
  starterPrompts: { label: string; text: string }[]
  agentSeat: { handle?: string } | undefined
  agentName: string
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
  outbox: Outgoing[]
  /** 队友正在写、还没发出的那几条（useTypingPreview），接在发件箱后面。 */
  typing: { block: Block; edge: RunEdge }[]
  editingId: string | null
  editSaving: boolean
  askStates?: Record<string, AskFormState>
  askGroups?: Record<string, AskGroupState>
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
  /** 在干活（或刚干完）的队友此刻的表情，按 handle（lib/agentFace）。 */
  agentFaces?: Record<string, AgentFace>
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
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest): void
  (e: 'ask-action', block: Block, action: AskAction): void
  (e: 'ask-group-action', scope: AskGroupScope, action: AskGroupAction): void
  (e: 'checklist', block: Block, items: TodoItem[]): void
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

const groupFocus = reactive<Record<string, string>>({})
watch(
  () => props.flashId,
  (id) => {
    const block = props.rows.find((row) => row.block.id === id)?.block
    if (block) focusGroup(block)
  }
)
function focusGroup(block: Block) {
  const scope = groupOf(block)
  if (scope) groupFocus[groupKey(scope)] = block.id
}
function groupAnchor(block: Block): string | undefined {
  const scope = groupOf(block)
  if (!scope) return undefined
  const focus = groupFocus[groupKey(scope)]
  return focus && props.rows.some((row) => row.block.id === focus) ? focus : groupFor(block)?.anchor
}
function groupFor(block: Block): AskGroupState | undefined {
  const scope = groupOf(block)
  return scope ? props.askGroups?.[groupKey(scope)] : undefined
}

// 正在推进的清单：房间在跑时，每位队友最新的那一条。更早的清单即使还有一步停在
// 「正在做」，也是上一轮没走完的，不该跟着转。
const liveChecklists = computed(() => {
  const ids = new Set<string>()
  if (!props.working) return ids
  const seen = new Set<string>()
  for (let i = props.rows.length - 1; i >= 0; i--) {
    const b = props.rows[i].block
    if (!b.meta?.checklist || seen.has(b.author)) continue
    seen.add(b.author)
    ids.add(b.id)
  }
  return ids
})

// 同一位队友连着的几条事件行合成一段，和它连着说的几句话一样：只有第一条带头像、
// 名字和时间。断开的条件和消息一样（chatGrouping.ts）：中间插了别的行、换了一天、
// 隔了一小时以上。和消息之间照旧断开。
const noticeCont = computed(() =>
  props.rows.map((row, i) => {
    const prev = props.rows[i - 1]
    if (!row.notice || !prev?.notice) return false
    const agent = props.noticeAgent(row.block, row.notice)
    const before = props.noticeAgent(prev.block, prev.notice)
    if (!agent || !before || agent.name !== before.name || agent.handle !== before.handle) return false
    if (props.splitMarkers.before.has(row.block.id) || row.block.id === props.unreadAnchorId) return false
    if (dayKey(prev.block.created_at) !== dayKey(row.block.created_at)) return false
    return Date.parse(row.block.created_at) - Date.parse(prev.block.created_at) < REGROUP_GAP_MS
  })
)

// 队友在干活时，对话里它最近出现的那个头像跟着它的状态动：从下往上找，每位队友
// 只认第一个带头像的行——消息一组里的第一条，或者它那几条事件行里的第一条。
const faceRows = computed(() => {
  const faces = props.agentFaces ?? {}
  const wanted = Object.keys(faces).length
  const out = new Map<string, AgentFace>()
  const seen = new Set<string>()
  for (let i = props.rows.length - 1; i >= 0 && seen.size < wanted; i--) {
    const { block, notice } = props.rows[i]
    const handle = notice
      ? noticeCont.value[i]
        ? null
        : props.noticeAgent(block, notice)?.handle ?? null
      : props.runEdges[i] !== 'cont' && props.isAgentBlock(block)
        ? block.author
        : null
    if (!handle || !faces[handle] || seen.has(handle)) continue
    seen.add(handle)
    out.set(block.id, faces[handle])
  }
  return out
})

// 悬停在动的头像上：现场顶上那一行，连同已经用了多久。秒数只在有队友在跑时走。
const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const ticking = computed(() => [...faceRows.value.values()].some((f) => f.status))
watch(
  ticking,
  (on) => {
    clearInterval(ticker)
    now.value = Date.now()
    ticker = on ? setInterval(() => (now.value = Date.now()), 1000) : undefined
  },
  { immediate: true }
)
onBeforeUnmount(() => clearInterval(ticker))

function faceLabel(face: AgentFace | undefined): string | null {
  const status = face?.status
  if (!status) return null
  const label = siteStatusLabel(status)
  if (status.startedAt === null) return label
  const span = formatSpan(Math.max(0, Math.floor((now.value - status.startedAt) / 1000)))
  return `${label} · ${t('work.room.site.status.elapsed', { span })}`
}

// 读屏读到的那一句只有状态，不带秒数：头像的名字每秒一换，读屏就可能每秒再念一遍。
function faceStatus(face: AgentFace | undefined): string | null {
  return face?.status ? siteStatusLabel(face.status) : null
}

// Child rows emit the same events the panel listens for; the extra hop is what
// keeps this component free of the room's own bookkeeping. Thin wrappers so the
// template stays a table of rows instead of a wall of arrows.
function emitReact(block: Block, emoji: string) {
  emit('react', block, emoji)
}
function emitAskAction(block: Block, action: AskAction) {
  emit('ask-action', block, action)
}
function emitChecklist(block: Block, items: TodoItem[]) {
  emit('checklist', block, items)
}
function emitOpenFile(path: string, taskId: string | null) {
  emit('open-file', path, taskId)
}
function emitOpenResource(resource: string, turnId?: string, review?: DocReviewRequest) {
  emit('open-resource', resource, turnId, review)
}
// 只有正在改的那一行会存。
function emitSaveEdit(text: string) {
  const row = props.rows.find((r) => r.block.id === props.editingId)
  if (row) emit('save-edit', row.block, text)
}
// 行认的是自己身上的 data-row-id，不认模板里的 m：每一行的监听函数在每次重画时都是
// 同一个。捕获了 m 的箭头函数每次都是新的，Vue 就当这一行的 props 变了——往上拼一页
// 旧消息，已经在屏上的几百行会跟着全部重画一遍。
function settleRow(e: AnimationEvent) {
  const id = (e.currentTarget as HTMLElement | null)?.dataset.rowId
  if (id) emit('settle-arrival', e, id)
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
          :class="{ 'tl-arrive': arrived.has(m.id) }"
          :block="m"
          :notice="notice"
          :run="run"
          :agent="noticeAgent(m, notice)"
          :cont="noticeCont[i]"
          :face="faceRows.get(m.id)?.state ?? null"
          :face-label="faceLabel(faceRows.get(m.id))"
          :face-status="faceStatus(faceRows.get(m.id))"
          :time="fmtTime(notice.mode === 'agent-status' ? notice.updatedAt : m.created_at)"
          :agent-name="agentName"
          :refs="refs"
          :can-retry="i === retryIndex"
          :retrying="retryBusy"
          :project-id="topic?.project_id ?? null"
          :data-row-id="m.id"
          @animationend="settleRow"
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
          :ask-state="askStates?.[m.id]"
          :live="liveChecklists.has(m.id)"
          :face="faceRows.get(m.id)?.state ?? null"
          :face-label="faceLabel(faceRows.get(m.id))"
          :face-status="faceStatus(faceRows.get(m.id))"
          :editing="editingId === m.id"
          :edit-text="editingId === m.id ? editableText(m.content, refs) : undefined"
          :saving="editSaving"
          :data-row-id="m.id"
          @animationend="settleRow"
          @open-file="emitOpenFile"
          @open-topic="emit('open-topic', $event)"
          @open-card="emit('open-card', $event)"
          @react="emitReact"
          @ask-action="emitAskAction"
          @checklist="emitChecklist"
          @download="emit('download', $event)"
          @jump="emit('jump', $event)"
          @avatar-error="emit('avatar-error', $event)"
          @save-edit="emitSaveEdit"
          @cancel-edit="emit('cancel-edit')"
        >
          <template #ask-group>
            <AskGroupFlow
              v-if="groupAnchor(m) === m.id"
              :state="groupFor(m)!"
              :viewer="viewer"
              :names="refs.mentionNames"
              :focus-block="groupFocus[groupKey(groupFor(m)!.scope)] ?? m.id"
              :auto-focus="!!groupFocus[groupKey(groupFor(m)!.scope)]"
              @action="emit('ask-group-action', groupFor(m)!.scope, $event)"
            />
            <button v-else-if="groupFor(m)" type="button" @click="focusGroup(m)">{{ t('ask.group.open') }}</button>
          </template>
        </RoomMessage>
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

      <!-- 队友正在写的那条：和它落下来之后同一个样子，淡一档，时间那一格写「正在输入…」。
           它还不是消息，所以没有悬停条、没有表情、不进未读。 -->
      <RoomMessage
        v-for="{ block: m, edge } in typing"
        :key="m.id"
        :block="m"
        :parent="null"
        :parent-name="null"
        :run-start="edge !== 'cont'"
        :regroup="edge === 'regroup'"
        :mine="false"
        :topic-id="topic?.id ?? null"
        :author-name="displayName(m)"
        :external="isExternal(m.author)"
        :avatar="avatarSrc(m.author)"
        :is-agent="isAgentBlock(m)"
        :time="t('work.room.chat.typing')"
        :refs="refs"
        :viewer="viewer"
        :ask-busy="false"
        :outgoing="{ failed: false }"
        @avatar-error="emit('avatar-error', $event)"
      />

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
  /* 动作按这一列的可用宽度收起，桌面分栏也能比手机视口窄。 */
  container: chat-timeline / inline-size;
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
