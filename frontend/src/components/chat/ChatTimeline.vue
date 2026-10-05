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
import type { AskAction, AskFormState } from '../../lib/askPresentation'
import type { RunEdge } from '../../lib/chatGrouping'
import type { Outgoing } from '../../lib/composerDrafts'
import type { DocReviewRequest } from '../../lib/docReview'
import type { NoticeAgent, NoticeRow, PlatformNotice } from '../../lib/platformNotice'
import type { SplitMarker } from '../../lib/splitMarkers'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { replySnippet } from '../../lib/blockDisplay'
import { dayKey, REGROUP_GAP_MS } from '../../lib/chatGrouping'
import { editableText } from '../../lib/renderMessage'
import { formatSpan } from '../../lib/siteLog'
import { siteStatusLabel } from '../../lib/siteStatusLabel'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import DispatchedMarker from '../DispatchedMarker.vue'
import RoomHoverBar from '../room/RoomHoverBar.vue'
import RoomMessage from '../room/RoomMessage.vue'
import RoomNotice from '../room/RoomNotice.vue'
import TimelineMark from '../TimelineMark.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topic: Topic | null
  rows: NoticeRow[]
  /** 开头还没挂上的行数（首屏分批挂行，room/composables/useRowBatch）。 */
  hiddenRows?: number
  dayLabels: Map<string, string>
  unreadAnchorId: string | null
  splitMarkers: { before: Map<string, SplitMarker[]>; tail: SplitMarker[] }
  runEdges: RunEdge[]
  arrived: Set<string>
  delivered: Set<string>
  sentNow: Set<string>
  flashId: string | null
  timeShownId: string | null
  bar: { id: string | null; shown: boolean; top: number; jump: boolean; menuAt?: { x: number; y: number } | null }
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
// ---- 读屏播报：新到的一整条消息念一句「谁：开头一段」 ----
// 整条时间线不做 aria-live：历史加载、翻页、流式输出都会让读屏一直念。只在一条别人
// 发的消息「刚到、并且出现在屏幕上」（`arrived`）时换一句话；正在写的那条（typing
// 预览）不在 rows 里，不会被念。
const liveAnnouncement = ref('')
watch(
  () => props.rows.at(-1)?.block.id,
  (id) => {
    const row = props.rows.at(-1)
    if (!id || !row || row.notice || !props.arrived.has(id)) return
    const block = row.block
    if (props.isMine(block) || (block.kind !== 'message' && block.kind !== 'attachment')) return
    liveAnnouncement.value = t('work.room.chat.liveNew', {
      name: props.displayName(block),
      text: replySnippet(block, props.refs, 140),
    })
  }
)

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
  <!-- Focusable so keyboard users can scroll it with the arrow and page keys (listed in the shortcut sheet). -->
  <div
    :ref="scrollRef"
    class="messages flex-grow-1 overflow-y-auto py-2"
    data-testid="chat-scroll"
    tabindex="0"
    role="region"
    :aria-label="t('work.room.chat.timelineLabel')"
  >
    <!-- Screen readers hear one line per newly arrived message (who + the start of it), never the streaming text. -->
    <p class="visually-hidden" role="status" aria-live="polite" data-testid="chat-live">{{ liveAnnouncement }}</p>
    <!-- Single wrapper so a ResizeObserver can watch the timeline's total
             content height (rows + streaming bubble + timeline-end slot). -->
    <div :ref="contentRef" class="tl-content">
      <RoomHoverBar
        :block="barBlock"
        :shown="bar.shown"
        :top="bar.top"
        :jump="bar.jump"
        :menu-at="bar.menuAt ?? null"
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
          <BaseButton
            v-for="prompt in starterPrompts"
            :key="prompt.label"
            kind="secondary"
            size="sm"
            @click="emit('starter', prompt.text)"
            >{{ prompt.label }}</BaseButton
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
        <!-- Leading rows wait for the first-paint batch (useRowBatch); every row ends up in the DOM. -->
        <template v-if="i >= (hiddenRows ?? 0)">
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
          />
        </template>
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
  /* 对话是连续阅读，收成读的一栏（--page-w-read）居中。桌面上房间常只占 80% 的
     宽，2560 上铺满会排到 1700 一行，回行时眼睛找不到下一行的开头。代码块和表格
     本来就 max-width:100% + 横向滚动（RoomMessage 的 .md-content），这一栏收窄
     后照旧读得动。滚动的仍是整块（.messages），两边的空白手指照样拖得动。 */
  width: 100%;
  max-width: var(--page-w-read);
  margin-inline: auto;
}
/* 手机外壳里那三块（时间线、输入框、贴在输入框上的那一条）收成同一栏 --page-w
   （720，比阅读栏松一点，平板竖屏上才不至于一宽一窄）；输入框和那一条的收法见
   ChatPanel / TopicChatColumn。桌面上三块一起用上面的 --page-w-read。 */
@media (max-width: 959.98px) {
  .tl-content {
    max-width: var(--page-w);
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

.messages:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
</style>
