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
import type { MentionPoolEntry } from '../../composables/useRoomMentionPicker'
import type { Block, ReplyPreview, TodoItem, Topic } from '../../cx_types'
import type { AgentFace } from '../../lib/agentFace'
import type { GroupAnswer } from '../../lib/askGroups'
import type { TaskLine } from '../../lib/channelTasks'
import type { RunEdge } from '../../lib/chatGrouping'
import type { Outgoing } from '../../lib/composerDrafts'
import type { DocReviewRequest } from '../../lib/docReview'
import type { OpenedDocument } from '../../lib/docReview'
import type { NoticeAgent, NoticeRow, PlatformNotice } from '../../lib/platformNotice'
import type { RefNames } from '../../lib/refChip'
import type { AskAction } from '../../types/ask'
import type { ChapterRow } from '../../types/chapters'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import { replySnippet } from '../../lib/blockDisplay'
import { inReadingOrder } from '../../lib/chapters'
import { dayKey, REGROUP_GAP_MS } from '../../lib/chatGrouping'
import { docLineInTurn, isDocLine } from '../../lib/docEditLine'
import { noticeLine } from '../../lib/noticeLine'
import { rowKeys } from '../../lib/platformNotice'
import { formatSpan } from '../../lib/siteLog'
import { siteStatusLabel } from '../../lib/siteStatusLabel'
import AskGroupCard from '../ask/AskGroupCard.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import RoomHoverBar from '../room/RoomHoverBar.vue'
import RoomMessage from '../room/RoomMessage.vue'
import RoomNotice from '../room/RoomNotice.vue'
import TaskCard from '../room/TaskCard.vue'
import TaskCreatedPost from '../room/TaskCreatedPost.vue'
import ThreadLine from '../room/ThreadLine.vue'
import TimelineMark from '../TimelineMark.vue'

import ChapterMark from './ChapterMark.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import RoutineRunLine from '@/components/routine/RoutineRunLine.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topic: Topic | null
  /** 这里已经不能回答提问了（任务关了、频道归档了）。 */
  askClosed?: boolean
  /** 私聊里的消息不能转为任务：不给「转为任务」。 */
  noUpgrade?: boolean
  /** 这是频道的主线：消息可以有支线，下面挂着支线那一行。 */
  threadable?: boolean
  /** 这条消息的支线里谁正在回复（还没有回复时）；没有人时为 null。 */
  replyingFor?: (m: Block) => string | null
  /** 那几位队友的 handle：支线那一行的头像里要有它们。 */
  replyingSeatsFor?: (m: Block) => string[]
  /** 那位队友此刻在等什么（排队、重试……）；没有就是 null。 */
  threadStatusFor?: (m: Block) => string | null
  /** 支线里：AI 队友的回复可以看它那一轮的过程。 */
  processable?: boolean
  /** 支线和任务里的对话：人发的长消息也收起（RoomMessage 的 `foldPlain`）。 */
  foldPlain?: boolean
  /** 一个 handle 叫什么（支线那一行写最后一句是谁说的）。 */
  nameOf?: (handle: string) => string
  rows: NoticeRow[]
  /** 组头块的 id → 这一组的每道题（`lib/askGroups`）；组里其余几行不单独画。 */
  askGroups?: Map<string, Block[]>
  /** 整组提交在路上 / 失败时那一句：由面板拿着（`useAskGroupAnswer`）。 */
  askGroupBusy?: boolean
  askGroupFailure?: string | null
  /** 开头还没挂上的行数（首屏分批挂行，room/composables/useRowBatch）。 */
  hiddenRows?: number
  dayLabels: Map<string, string>
  unreadAnchorId: string | null
  runEdges: RunEdge[]
  arrived: Set<string>
  delivered: Set<string>
  sentNow: Set<string>
  flashId: string | null
  timeShownId: string | null
  bar: {
    id: string | null
    shown: boolean
    top: number
    jump: boolean
    menuAt?: { x: number; y: number; selectedText?: string } | null
  }
  barBlock: Block | null
  barEditable: boolean
  /** 上一下是键盘在走这一列（useChatRowActions 的 kbdNav）：焦点框由我们自己画。 */
  kbdNav?: boolean
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
  starterPrompts: { label: string; hint: string; text: string }[]
  agentName: string
  refs: RefNames
  outbox: Outgoing[]
  /** 队友正在写、还没发出的那几条（useTypingPreview），接在发件箱后面。 */
  typing: { block: Block; edge: RunEdge }[]
  editingId: string | null
  editSaving: boolean
  /** 编辑那条消息时的 @ 候选：和房间输入区同一份名册、同一份话题表。
   *  只 @ 人、话题和群播——编辑框不放附件，所以没有资料库那一级。
   *  不填也画得出来：只有真在改一条消息时才用得上，预览站和别的用例挂的是
   *  静态的一张时间线。 */
  mentionPool?: MentionPoolEntry[]
  topicList?: Topic[]
  /** Bound with `:ref`, so the pane the panel measures is this one. */
  scrollRef: Ref<HTMLElement | null>
  contentRef: Ref<HTMLElement | null>
  /* Renderers that belong to the room, not to a row: names, avatars, times. */
  isAgentBlock: (b: Block) => boolean
  isMine: (m: Block) => boolean
  isExternal: (handle: string) => boolean
  /** handle → 头像 URL（没挑过、名册里没这个人就是 null）。这一层不画头像，只把
      它转发给下面的 RoomMessage / ThreadLine，由它们的 UserAvatar 决定画脸还是画
      彩色首字母。原名叫 `avatarSrc`，改成 `avatarOf`：和这批修复里其它地方（ThreadLine、
      文档链）已经统一的名字一致，一个概念只有一个叫法。 */
  avatarOf: (handle: string) => string | null
  displayName: (m: Block | ReplyPreview) => string
  authorTitle?: (m: Block) => string | null
  noticeAgent: (m: Block, notice: PlatformNotice) => NoticeAgent | null
  parentOf: (m: Block) => Block | ReplyPreview | undefined
  showReplyCue: (m: Block) => boolean
  fmtTime: (iso: string) => string
  pendingBlock: (item: Outgoing) => Block
  outgoingState: (item: Outgoing) => string
  outboxEdge: (index: number) => RunEdge
  myName: string
  viewer: string
  /** 在干活（或刚干完）的队友此刻的表情，按 handle（lib/agentFace）。 */
  agentFaces?: Record<string, AgentFace>
  /** 频道主线、我能在这里说话：消息和文件能置顶到频道。 */
  pinnable?: boolean
  /** 已经置顶的那几条（block id）。 */
  pinnedIds?: ReadonlySet<string>
  /** 这段对话能钉章节（频道主线、任务）：消息的 ⋯ 里多一项「钉成章节」。 */
  chapterable?: boolean
  /** 已经钉成章节的那几条（block id）。 */
  chapterIds?: ReadonlySet<string>
  /** 这段对话的章节，按对话里的顺序；每条起头消息前画一道章节刻度。 */
  chapters?: ChapterRow[]
  /** 频道里一件任务的那张卡；不认得就是 null。 */
  taskOf?: (taskId: string) => TaskLine | null
  /** 从这条消息（或它的支线）出来的任务，按创建的先后。 */
  tasksUnder?: (blockId: string) => TaskLine[]
}>()

const emit = defineEmits<{
  (e: 'react', block: Block, emoji: string): void
  (e: 'reply', block: Block): void
  (e: 'open-thread', block: Block): void
  (e: 'open-process', turnId: string): void
  (e: 'upgrade-message', messageId: string): void
  (e: 'edit', block: Block): void
  (e: 'edit-send', item: Outgoing): void
  (e: 'toggle-picker', blockId: string): void
  (e: 'open-file', path: string): void
  (e: 'open-card', taskId: string): void
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
  (e: 'ask-reply', block: Block, text: string): void
  (e: 'ask-withdraw', block: Block): void
  (e: 'ask-skip', block: Block): void
  (e: 'ask-decide', block: Block, action: AskAction): void
  (e: 'ask-group-submit', questionId: string, answers: Record<string, GroupAnswer>): void
  (e: 'checklist', block: Block, items: TodoItem[]): void
  (e: 'download', block: Block): void
  (e: 'jump', blockId: string): void
  (e: 'save-edit', block: Block, text: string): void
  (e: 'cancel-edit'): void
  (e: 'retry'): void
  (e: 'pin', block: Block): void
  (e: 'unpin', block: Block): void
  (e: 'chapter', block: Block): void
  (e: 'unchapter', block: Block): void
  (e: 'keep', block: Block): void
  (e: 'retry-send', clientId: string): void
  (e: 'starter', text: string): void
  (e: 'settle-arrival', event: AnimationEvent, id: string): void
  (e: 'settle-sent', event: AnimationEvent, clientId: string): void
  (e: 'outbox-leave', el: Element, done: () => void): void
}>()

// 每一行的 key 沿用它上一次的（lib/platformNotice rowKeys）：合成的那一行两头又并进
// 来一条时，人点开的那一串不被换成一行新的、收起来的。上一次的名字只在这里读写，
// 不是要画的东西，所以不放进响应式状态。
let keyedBefore: ReadonlyMap<string, string> = new Map()
const rowKeyList = computed(() => {
  const { keys, byBlock } = rowKeys(props.rows, keyedBefore)
  keyedBefore = byBlock
  return keys
})

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

// 合成一行的那一串署谁的名，看它最后那一条。改文档的那一行不在哪一轮里时不署名
// （lib/docEditLine）：它是一行记录，不是哪位队友说的话，后面接着的事件行也不能当它
// 是那位队友的续行。
function agentOf(block: Block, notice: PlatformNotice): NoticeAgent | null {
  const own = notice.mode === 'repeats' ? notice.rows[notice.rows.length - 1] : { block, notice }
  if (!own.notice) return null
  if (isDocLine(own.block) && !docLineInTurn(own.block)) return null
  return props.noticeAgent(own.block, own.notice)
}

// 「新建了任务」那一行：认得出那件任务时，画成新建它的人发出的一张任务卡。
function createdTask(row: NoticeRow): TaskLine | null {
  const notice = row.notice
  if (!notice || notice.mode !== 'action' || !['split', 'task_created'].includes(notice.resource)) return null
  const id = (row.block.meta as Record<string, unknown> | null | undefined)?.task_id
  return typeof id === 'string' ? props.taskOf?.(id) ?? null : null
}
const createdTasks = computed(() => props.rows.map(createdTask))
// 同一个人接连新建的几件，归在同一个名字下面：断开的条件和消息一样。
const createdCont = computed(() =>
  createdTasks.value.map((task, i) => {
    const before = createdTasks.value[i - 1]
    const row = props.rows[i]
    const prev = props.rows[i - 1]
    if (!task || !before || !task.creator || task.creator !== before.creator) return false
    if (row.block.id === props.unreadAnchorId || dayKey(prev.block.created_at) !== dayKey(row.block.created_at))
      return false
    return Date.parse(row.block.created_at) - Date.parse(prev.block.created_at) < REGROUP_GAP_MS
  })
)
const NO_TASKS: TaskLine[] = []
function ownerName(task: TaskLine): string | null {
  return task.owner ? (props.nameOf ?? String)(task.owner) : null
}
function under(blockId: string): TaskLine[] {
  return props.tasksUnder?.(blockId) ?? NO_TASKS
}
/** 这条消息的支线自己转成的那件任务：画在支线那一块的顶上，不另起一行。 */
function became(m: Block): TaskLine | undefined {
  return m.thread ? under(m.id).find((task) => task.id === m.thread?.id) : undefined
}
/** 从这条消息（或支线里的这句话）拆出去的任务，一件一行。 */
function madeFrom(m: Block): TaskLine[] {
  const tasks = under(m.id)
  if (!tasks.length) return NO_TASKS
  const own = props.threadable ? became(m) : undefined
  return own ? tasks.filter((task) => task !== own) : tasks
}

/**
 * 这道题上我有一条答复还压在发件箱里、没落库：卡片把选项先按住。
 *
 * 卡片收起要等那条消息落库、题块再带着答案回来，这一来一回里手快就会多点出几条消息，
 * 房间里真会多出几条。被服务端拒掉的那条不算：那一下没答上题，选项得还给人家重选
 * （他点那条消息上的「重试」，它回到在路上，卡片跟着再锁上）。
 */
function answeringQuestion(id: string): boolean {
  return props.outbox.some((item) => item.replyTo === id && item.state !== 'failed')
}

// 同一位队友连着的几条事件行合成一段，和它连着说的几句话一样：只有第一条带头像、
// 名字和时间。断开的条件和消息一样（chatGrouping.ts）：中间插了别的行、换了一天、
// 隔了一小时以上。和消息之间照旧断开。
const noticeCont = computed(() =>
  props.rows.map((row, i) => {
    const prev = props.rows[i - 1]
    // 一轮里改文档的那一行接在这一轮的话后面（chatGrouping 的 runEdgeBetween）。
    if (row.notice && props.runEdges[i] === 'cont') return true
    if (!row.notice || !prev?.notice) return false
    const agent = agentOf(row.block, row.notice)
    const before = agentOf(prev.block, prev.notice)
    if (!agent || !before || agent.name !== before.name || agent.handle !== before.handle) return false
    if (row.block.id === props.unreadAnchorId) return false
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
    // 平台替队友说的那一行头像列里只是一枚小头像，不是它的头像：表情不放在那儿。
    const handle = notice
      ? noticeCont.value[i] || block.shown_as === 'for_agent'
        ? null
        : agentOf(block, notice)?.handle ?? null
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
const ticking = computed(() => [...faceRows.value.values()].some((f) => f.status && f.status.startedAt !== null))
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

const faceLabels = computed(() => {
  const out = new Map<string, string>()
  for (const [id, face] of faceRows.value) {
    const label = faceLabel(face)
    if (label) out.set(id, label)
  }
  return out
})
const faceStatuses = computed(() => {
  const out = new Map<string, string>()
  for (const [id, face] of faceRows.value) {
    const status = faceStatus(face)
    if (status) out.set(id, status)
  }
  return out
})

// Child rows emit the same events the panel listens for; the extra hop is what
// keeps this component free of the room's own bookkeeping. Thin wrappers so the
// template stays a table of rows instead of a wall of arrows.
function emitReact(block: Block, emoji: string) {
  emit('react', block, emoji)
}
function emitAskReply(block: Block, text: string) {
  emit('ask-reply', block, text)
}
function emitAskWithdraw(block: Block) {
  emit('ask-withdraw', block)
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
function emitOpenFile(path: string) {
  emit('open-file', path)
}
function emitOpenResource(resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument) {
  emit('open-resource', resource, turnId, review, document)
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
// 事件行和「新建了任务」那一条，读屏读到的名字和消息一样：谁、几点、说了什么（没有署名
// 的只有后两样）。由这一条本身算出来，不指向行里画出来的字：离屏的行不渲染
// （content-visibility），Chrome 算不出被跳过的内容，指向自己的那些行在屏幕外就没有名字。
function rowLabel(name: string | null, iso: string, text: string): string {
  const time = props.fmtTime(iso)
  return name ? t('work.room.chat.messageLabel', { name, time, text }) : t('work.room.chat.eventLabel', { time, text })
}
// 章节刻度：每条被钉成章节的消息是第几章、叫什么。优先用书签自己的名字（人改的或模型起的），
// 没有时退回那条消息的第一行；不在当前窗口里的章节也照样占一个号，翻动历史时刻度上的号不会跳。
const chapterMarks = computed(() => {
  const marks = new Map<string, { no: number; title: string }>()
  inReadingOrder(props.chapters ?? []).forEach((chapter, i) => {
    marks.set(chapter.block.id, {
      no: i + 1,
      title: chapter.title || replySnippet(chapter.block, props.refs, 60) || t('work.channel.chapters.untitled'),
    })
  })
  return marks
})
function chapterMarkOf(blockId: string) {
  return chapterMarks.value.get(blockId) ?? null
}

const rowLabels = computed(() =>
  props.rows.map(({ block, notice }, i) => {
    const task = createdTasks.value[i]
    if (task) {
      const creator = task.creator ? (props.nameOf ?? String)(task.creator) : null
      return rowLabel(creator, block.created_at, `${t('work.room.taskCard.created')} ${task.title}`)
    }
    if (!notice) return ''
    const agent = agentOf(block, notice)?.name ?? null
    return rowLabel(agent, block.created_at, noticeLine(block, notice, agent, props.refs))
  })
)
</script>

<template>
  <!-- Message stream — Feishu group chat: left-aligned rows, grouped runs,
           per-row hover action bar, centered system/event lines. -->
  <!-- 滚动区本身不是 Tab 停靠点：键盘停在下面那一列消息里的一条上，↑↓ 在消息之间走，
       走到哪就滚到哪（useTimelineKeys）。 -->
  <div :ref="scrollRef" class="messages flex-grow-1 overflow-y-auto py-2" data-testid="chat-scroll">
    <!-- Screen readers hear one line per newly arrived message (who + the start of it), never the streaming text. -->
    <p class="visually-hidden" role="status" aria-live="polite" data-testid="chat-live">{{ liveAnnouncement }}</p>
    <!-- Single wrapper so a ResizeObserver can watch the timeline's total
             content height (the host's start slot + rows + streaming bubble +
             timeline-end slot). -->
    <div :ref="contentRef" class="tl-content">
      <!-- Start of the conversation — the host puts what stands above the first
             message here (a thread's opening message). Inside the scroller, with
             the rows: it scrolls with them rather than pinning above them, where
             a long one would push the replies out and carry its own 收起 out of
             reach. -->
      <slot name="timeline-start" />
      <RoomHoverBar
        :block="barBlock"
        :shown="bar.shown"
        :top="bar.top"
        :jump="bar.jump"
        :menu-at="bar.menuAt ?? null"
        :is-agent="!!barBlock && isAgentBlock(barBlock)"
        :picker-open="!!barBlock && reactionPickerFor === barBlock.id"
        :editable="barEditable"
        :no-upgrade="noUpgrade"
        :threadable="threadable"
        :processable="processable"
        :pinnable="pinnable"
        :pinned-ids="pinnedIds"
        :chapterable="chapterable"
        :chaptered-ids="chapterIds"
        @react="emitReact"
        @toggle-picker="emit('toggle-picker', $event)"
        @reply="emit('reply', $event)"
        @thread="emit('open-thread', $event)"
        @process="emit('open-process', $event)"
        @upgrade="emit('upgrade-message', $event)"
        @edit="emit('edit', $event)"
        @pin="emit('pin', $event)"
        @unpin="emit('unpin', $event)"
        @chapter="emit('chapter', $event)"
        @unchapter="emit('unchapter', $event)"
      />
      <!-- 骨架和真的那几行同形同高：到货时骨架淡出，不推动下面的东西。 -->
      <Transition name="tl-skel">
        <LoadingSkeleton v-if="loadingHistory" variant="chat" />
      </Transition>

      <!-- 起手卡：项目本体还没跟 AI 队友说上话时，四件具体的事，点一张就把一句写好的请求
           放进输入框，人改完再发。新账号登录后第一屏就是这里（router/home.ts）。 -->
      <section v-if="showStarters" class="chat-start" :aria-label="t('work.room.chat.startAria')">
        <h2 class="t-page-title chat-start__title">{{ t('work.room.chat.startTitle') }}</h2>
        <div class="chat-start__cards">
          <button
            v-for="prompt in starterPrompts"
            :key="prompt.label"
            type="button"
            class="chat-start__card"
            @click="emit('starter', prompt.text)"
          >
            <span class="t-title">{{ prompt.label }}</span>
            <span class="t-body c-faint">{{ prompt.hint }}</span>
          </button>
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

      <!-- 一列消息（WAI-ARIA feed）：每一条是一个 article，整列只有当前那一条在 Tab 序列里。
           没有自己的盒子样式，排版和直接排在 .tl-content 里一样。 -->
      <div
        class="tl-feed"
        :class="{ 'tl-feed--kbd': kbdNav }"
        data-chat-feed
        role="feed"
        :aria-label="t('work.room.chat.timelineLabel')"
        :aria-busy="loadingHistory || loadingOlder"
      >
        <template v-for="({ block: m, notice, run }, i) in rows" :key="rowKeyList[i]">
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
            <!-- 章节刻度：这一节从这条消息开始。 -->
            <template v-if="!notice && chapterMarkOf(m.id)">
              <ChapterMark :no="chapterMarkOf(m.id)!.no" :title="chapterMarkOf(m.id)!.title" />
            </template>
            <TaskCreatedPost
              v-if="createdTasks[i]"
              :class="{ 'tl-arrive': arrived.has(m.id) }"
              :task="createdTasks[i]!"
              :creator="createdTasks[i]!.creator"
              :creator-name="(nameOf ?? String)(createdTasks[i]!.creator ?? '')"
              :owner-name="ownerName(createdTasks[i]!)"
              :avatar="avatarOf(createdTasks[i]!.creator ?? '')"
              :time="fmtTime(m.created_at)"
              :cont="createdCont[i]"
              :data-row-id="m.id"
              :data-feed-item="m.id"
              role="article"
              tabindex="-1"
              :aria-label="rowLabels[i]"
              @open="emit('open-card', $event)"
            />
            <RoomNotice
              v-else-if="notice"
              :class="{ 'tl-arrive': arrived.has(m.id) }"
              :block="m"
              :notice="notice"
              :run="run"
              :agent="agentOf(m, notice)"
              :time-of="fmtTime"
              :cont="noticeCont[i]"
              :face="faceRows.get(m.id)?.state ?? null"
              :face-label="faceLabels.get(m.id) ?? null"
              :face-status="faceStatuses.get(m.id) ?? null"
              :time="fmtTime(m.created_at)"
              :agent-name="agentName"
              :refs="refs"
              :can-retry="i === retryIndex"
              :retrying="retryBusy"
              :project-id="topic?.project_id ?? null"
              :data-row-id="m.id"
              :data-feed-item="m.id"
              role="article"
              tabindex="-1"
              :aria-label="rowLabels[i]"
              @animationend="settleRow"
              @open-resource="emitOpenResource"
              @open-card="emit('open-card', $event)"
              @retry="emit('retry')"
              @jump="emit('jump', $event)"
            />
            <!-- 一次问的几道题：收成一张卡画（`lib/askGroups` 把同组的行并到组头）。 -->
            <section
              v-else-if="!notice && askGroups?.get(m.id)"
              class="tl-ask-group"
              :class="{ 'tl-arrive': arrived.has(m.id) }"
              :data-row-id="m.id"
              :data-feed-item="m.id"
              role="article"
              tabindex="-1"
              :aria-label="rowLabels[i]"
            >
              <AskGroupCard
                :blocks="askGroups!.get(m.id)!"
                :asker="displayName(m)"
                :names="refs.mentionNames"
                :viewer="viewer"
                :closed="askClosed"
                :busy="askGroupBusy"
                :failure="askGroupFailure"
                @submit="(payload) => emit('ask-group-submit', payload.questionId, payload.answers)"
              />
            </section>
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
              :author-title="authorTitle?.(m) ?? null"
              :external="isExternal(m.author)"
              :avatar="avatarOf(m.author)"
              :is-agent="isAgentBlock(m)"
              :time="fmtTime(m.created_at)"
              :refs="refs"
              :name-of-handle="nameOf"
              :viewer="viewer"
              :ask-closed="askClosed"
              :ask-answering="answeringQuestion(m.id)"
              :active="bar.shown && bar.id === m.id"
              :live="liveChecklists.has(m.id)"
              :face="faceRows.get(m.id)?.state ?? null"
              :face-label="faceLabels.get(m.id) ?? null"
              :face-status="faceStatuses.get(m.id) ?? null"
              :editing="editingId === m.id"
              :saving="editSaving"
              :mention-pool="mentionPool"
              :topic-list="topicList"
              :pinnable="pinnable"
              :pinned="!!pinnedIds?.has(m.id)"
              :fold-plain="foldPlain"
              :data-row-id="m.id"
              :data-feed-item="m.id"
              @animationend="settleRow"
              @open-file="emitOpenFile"
              @open-card="emit('open-card', $event)"
              @react="emitReact"
              @ask-reply="emitAskReply"
              @ask-withdraw="emitAskWithdraw"
              @ask-skip="emit('ask-skip', $event)"
              @ask-decide="(block, action) => emit('ask-decide', block, action)"
              @checklist="emitChecklist"
              @download="emit('download', $event)"
              @jump="emit('jump', $event)"
              @save-edit="emitSaveEdit"
              @cancel-edit="emit('cancel-edit')"
              @keep="emit('keep', $event)"
              @pin="emit('pin', $event)"
              @unpin="emit('unpin', $event)"
            />
            <!-- 例行任务的一次执行：哪条规则、跑得怎么样、结果文件在哪，去看这次执行的支线。 -->
            <RoutineRunLine
              v-if="!notice && m.routine_run"
              class="tl-thread"
              :data-feed-of="m.id"
              :run="m.routine_run"
              :threaded="!!threadable && !!m.thread"
              @open="emit('open-thread', m)"
              @open-file="emitOpenFile"
            />
            <!-- 主线上这条消息的支线：和正文同一栏，挂在消息下面。不用 RoomMessage 的插槽：
               带插槽的行每次重画都会跟着重画。 -->
            <!-- 这条消息下面合成一块：上面是它的支线（转成了任务就多一行任务名），下面一件
                 一行是从这里拆出去的任务。支线里的回复下面，是从那句话拆出去的任务。 -->
            <div
              v-if="!notice && ((threadable && m.thread) || under(m.id).length)"
              class="tl-thread msg-work"
              :class="{ 'msg-work--mine': became(m)?.mine }"
              :data-feed-of="m.id"
            >
              <div v-if="threadable && m.thread" class="msg-work__talk">
                <TaskCard
                  v-if="became(m)"
                  v-guide-anchor="'task-card'"
                  :task="became(m)!"
                  :owner-name="ownerName(became(m)!)"
                  head
                  @open="emit('open-card', $event)"
                />
                <ThreadLine
                  v-guide-anchor="
                    replyingFor?.(m) ? 'reply-coming' : m.thread?.failed ? 'reply-failed' : 'reply-thread'
                  "
                  :summary="m.thread ?? null"
                  :replying="replyingFor?.(m) ?? null"
                  :replying-seats="replyingSeatsFor?.(m) ?? []"
                  :status="threadStatusFor?.(m) ?? null"
                  :refs="refs"
                  :name-of="nameOf ?? String"
                  :avatar-of="avatarOf"
                  :time="m.thread?.last_reply_at ? fmtTime(m.thread.last_reply_at) : null"
                  @open="emit('open-thread', m)"
                />
              </div>
              <TaskCard
                v-for="task in madeFrom(m)"
                :key="task.id"
                v-guide-anchor="'task-card'"
                :task="task"
                :owner-name="ownerName(task)"
                :owner-avatar="task.owner ? avatarOf(task.owner) : undefined"
                in-list
                :lone="!(threadable && m.thread) && madeFrom(m).length === 1"
                @open="emit('open-card', $event)"
              />
            </div>
          </template>
        </template>

        <!-- 发件箱: 已经打出去、还没落库的消息。它长得就是一条自己发的消息,
             只是时间那一格写的是送达状态——「立即显示」是第一位的，送达状态是
             第二位的。 -->
        <TransitionGroup :css="false" @leave="emitOutboxLeave">
          <RoomMessage
            v-for="(item, oi) in outbox"
            :key="item.clientId"
            :class="{ 'tl-sent': sentNow.has(item.clientId) }"
            :data-cid="item.clientId"
            :data-feed-item="`out-${item.clientId}`"
            :block="pendingBlock(item)"
            :parent="null"
            :parent-name="null"
            :run-start="outboxEdge(oi) !== 'cont'"
            :regroup="outboxEdge(oi) === 'regroup'"
            :mine="true"
            :topic-id="topic?.id ?? null"
            :author-name="myName"
            :external="isExternal(viewer)"
            :avatar="avatarOf(viewer)"
            :is-agent="false"
            :time="outgoingState(item)"
            :refs="refs"
            :viewer="viewer"
            :ask-busy="false"
            :outgoing="{
              error: item.error,
              failed: item.state === 'failed',
              slow: !!item.slow || !!outgoingState(item),
            }"
            @animationend="emit('settle-sent', $event, item.clientId)"
            @retry="emit('retry-send', item.clientId)"
            @edit="emit('edit-send', item)"
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
          :author-title="authorTitle?.(m) ?? null"
          :external="isExternal(m.author)"
          :avatar="avatarOf(m.author)"
          :is-agent="isAgentBlock(m)"
          :time="t('work.room.chat.typing')"
          :refs="refs"
          :viewer="viewer"
          :ask-busy="false"
          :outgoing="{ failed: false }"
        />
      </div>

      <!-- End of the conversation timeline — GitHub PR's merge box. Host fills. -->
      <div class="px-4">
        <slot name="timeline-end" />
      </div>
    </div>
  </div>
</template>

<style scoped src="../room/room-row.css"></style>

<style scoped>
.chat-start {
  max-width: 560px;
  padding: 32px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.chat-start__title {
  margin: 0;
}
.chat-start__cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.chat-start__card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  cursor: pointer;
}
.chat-start__card:hover {
  background: var(--fill);
}
@media (max-width: 600px) {
  .chat-start__cards {
    grid-template-columns: minmax(0, 1fr);
  }
}
.messages {
  background: var(--surface);
  /* A flex child's implicit min-height is its content — without this, a long
     timeline refuses to shrink and pushes whatever follows (error alert,
     reply bar) below the pane edge, clipped. */
  min-height: 0;
}
/* 骨架到货时淡出。离场时它脱离文档流，下面已经排好的真行不会被它推一下。 */
/* 支线那一行和消息正文对齐：行的左内边距 16 + 头像 28 + 间距 10。 */
.tl-thread {
  margin-left: 54px;
  width: calc(100% - 70px);
}
/* 消息下面那一块：支线和任务长在一起，同一个底色。支线那几行在上，转成任务时顶上多
   一行任务名；拆出去的任务一件一行，排在下面。 */
.msg-work {
  display: flex;
  flex-direction: column;
  max-width: 620px;
  margin-top: 6px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: var(--fill);
  overflow: hidden;
}
.msg-work--mine {
  border-color: var(--warn);
}
.msg-work > * + * {
  border-top: 1px solid var(--line);
}
/* 任务名那一行和下面的回复是同一段对话：一起变色，点哪儿都打开它。 */
.msg-work__talk {
  display: flex;
  flex-direction: column;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.msg-work__talk:hover {
  background: var(--line-2);
}
.msg-work__talk > :deep(.task-card--head + .thread-line) {
  padding-top: 2px;
}
.msg-work :deep(.thread-line),
.msg-work :deep(.thread-line:hover) {
  max-width: none;
  margin-top: 0;
  background: transparent;
}
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

/* 焦点框不交给浏览器的 :focus-visible 判据：它见到任何一次按键都算「人在用键盘」，
   有的系统还会把音量/媒体键也递进网页——点过一条消息再按一下音量键（只想调音量），
   整行就亮起一圈。这里只认这一列自己用的那几个键（useTimelineKeys 的 isTimelineKey），
   由 kbdNav 决定画不画。环画在行内（offset 取负），和 RoomMessage 的 .im-row 一样——
   行是整宽的，滚动区会把画在外面的环左右切掉。 */
.tl-feed > [data-feed-item]:focus-visible {
  outline: none;
}
.tl-feed.tl-feed--kbd > [data-feed-item]:focus {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
</style>
