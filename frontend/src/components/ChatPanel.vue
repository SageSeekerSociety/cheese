<script setup lang="ts">
import type {
  AgentControlState,
  Block,
  ChatAttachment,
  ProjectMemberRow,
  ReactionAgg,
  RoomTask,
  TodoItem,
  Topic,
  WsServerFrame,
} from '../cx_types'
import type { ComposerMemory, Outgoing, StoredComposerDraft } from '../lib/composerDrafts'

import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import {
  answerOptions,
  ApiError,
  attachmentRawUrl,
  downloadFile,
  ensureFreshToken,
  getProgress,
  isRetryableGetFailure,
  listBlocks,
  listRoomTasks,
  summonAgent,
  toggleReaction as apiToggleReaction,
  undoTopicTitle,
} from '../api'
import { uploaded, usePendingAttachments } from '../lib/attachments'
import { isAgentBlock, isAgentHandle, isPersonBlock } from '../lib/authorship'
import { cachedWindow, pendingBlockRefresh, setCachedWindow } from '../lib/blockCache'
import { replySnippet } from '../lib/blockDisplay'
import { mergeRefreshedTail, PAGE_SIZE, prependOlder, scrollTopAfterPrepend, shouldLoadOlder } from '../lib/blockPaging'
import { loadComposerDraft, loadComposerMemory, saveComposerDraft, saveComposerMemory } from '../lib/composerDrafts'
import { AGENT_STATUS_EVENTS, collapseNotices, type PlatformNotice } from '../lib/platformNotice'
import { coalesceSplitFencedCodeBlocks } from '../lib/renderMessage'
import { placeSplitMarkers } from '../lib/splitMarkers'
import { topicShortId, topicStateBadge } from '../lib/topicState'
import { myHandle } from '../me'

import LoadingSkeleton from './common/LoadingSkeleton.vue'
import { useChatScroll } from './room/composables/useChatScroll'
import { useOutbox } from './room/composables/useOutbox'
import { useRoomRoster } from './room/composables/useRoomRoster'
import { useRoomSocket } from './room/composables/useRoomSocket'
import RollingNumber from './room/RollingNumber.vue'
import RoomComposer from './room/RoomComposer.vue'
import RoomHoverBar from './room/RoomHoverBar.vue'
import RoomMessage from './room/RoomMessage.vue'
import RoomNotice from './room/RoomNotice.vue'
import CheeseAvatar from './CheeseAvatar.vue'
import DispatchedMarker from './DispatchedMarker.vue'
import TimelineMark from './TimelineMark.vue'

import { t } from '@/i18n'

// Message rendering (markdown / plain / reference chips) lives in
// ../lib/renderMessage and happens in the row components; here we only fill the
// handle→name and id→title maps they render with, from the roster / topics props.
const mentionNames = reactive<Record<string, string>>({})
const topicTitles = reactive<Record<string, string>>({})
const refMaps = { mentionNames, topicTitles }

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
// without a manual reload (spec §7.1 实时联动). `turn-done` fires when a turn
// completes.
const emit = defineEmits<{
  // 标题左边那颗 ← 被按了。去哪儿由拥有这个地址的人决定，不是这里。
  (e: 'back'): void
  // A cheese command changed a platform resource (doc/decision/topics/...) —
  // the parent refreshes that panel live, mid-turn.
  (e: 'state-changed', resource: string): void
  (e: 'turn-done'): void
  // 芝士 是不是正在这个话题里干活。跟着轮次生命周期走（summon / turn_started /
  // turn_active 开，turn_finished / done / error 关），不是跟着它第一次动手
  // 走：干出来的东西是干活的**证据**，不是干活的**开始**，而右边那格「现场」得
  // 在开工那一刻就在那儿——它就是用来看它在干什么的。
  (e: 'working', working: boolean): void
  // 会话控制状态（任务、模型）动了：socket 上的这一帧转给现场那格的控制条。
  (e: 'agent-control', state: AgentControlState): void
  // 现场那格的时间线上多了一行，或者已有的一行变了（挂了、重试次数涨了）。socket
  // 在这一栏，现场自己听不到。
  (e: 'site-block', block: Block): void
  // 正在跑的轮次，各自从什么时候开始（毫秒）。现场的状态条靠它说「已用多久」。
  (e: 'site-turns', turns: Record<string, number>): void
  // ⤴ 升级为话题 (eval A1): the parent upgrades this message block into a topic.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
  // A task in this room (dispatched marker, upgraded message, <#task> chip): opens its card here.
  (e: 'open-card', taskId: string): void
  // A clicked @mention chip (resolved by the parent: person → member page,
  // topic/doc → open that topic).
  (e: 'mention-click', name: string): void
  // A <&path> file chip was clicked — the parent opens it in the 文件 drawer.
  (e: 'open-file', path: string, taskId?: string | null): void
  // An action card's button (decision → decisions page, milestone → calendar…).
  (e: 'open-resource', resource: string, turnId?: string): void
}>()

const AUTHOR = myHandle()

// 这一条出错就写给用户看，所以它得在下面那段（房间名册）之前。
const errorMsg = ref<string | null>(null)

// 名册、座位、显示名、头像 —— 见 room/composables/useRoomRoster。
// 房间名册和项目名册是两份，因为 AI 队友的座位只在前者上。
const {
  agentSeat,
  agentName,
  mentionPool,
  memberByHandle,
  seatByHandle,
  agentDisplayName,
  displayName,
  isExternal,
  avatarSrc,
  onAvatarError,
  myName,
} = useRoomRoster({
  topic: () => props.topic,
  members: () => props.members,
  author: AUTHOR,
  onError: (message) => {
    errorMsg.value = message
  },
})

// 输入框那一行提示语。和芝士私聊时它**不能**说「交给它做」：私聊不占机器，那边
// 的芝士没有工具，读不了文件也跑不了命令。一句承诺它做不到的事的提示语，换来的
// 是一次「我试了但做不了」，而人只会记得是它没做成。
const composerHint = computed(() =>
  props.alwaysSummon ? `给${agentName.value}发消息` : `输入消息，@${agentName.value} 交给它处理`
)

// Keep the module-level handle→name map in sync with the roster, so
// <@handle> tokens render with the member's display name.
watch(
  mentionPool,
  (pool) => {
    for (const k of Object.keys(mentionNames)) delete mentionNames[k]
    for (const row of pool) mentionNames[row.handle] = row.label
    // 群播 tokens (fusion-design §3): <@all>/<@here> render as friendly chips,
    // not the raw literal — they are reserved handles, not roster members.
    mentionNames.all = '所有人'
    mentionNames.here = '在线成员'
  },
  { immediate: true, deep: true }
)

const messages = ref<Block[]>([])
const loadingHistory = ref(false)

// Slack-style discrete messages: 芝士 doesn't stream tokens — each complete
// message lands as an `assistant_block` frame. `awaitingReply` drives the
// 正在看… indicator from summon until every active turn explicitly finishes.
const awaitingReply = ref(false)
// 这一轮的消息到没到芝士手上。平台收下和会话读到是两件事，中间隔着一次投递：
// 它可能失败退回队列，冷启动时还可能一分多钟里根本没有会话。所以这条指示分两
// 段说，翻页的那一下就是芝士的 👀 回执。
const reachedAgent = ref(false)
const activeTurnIds = ref<Set<string>>(new Set())
watch(awaitingReply, (v) => emit('working', v))
// 每个在跑的轮次从什么时候开始。中途连进来的，后端在 turn_active 上带着开始时间；
// 没带的（老后端）只能从连上的这一刻算。
const turnStarts = ref<Record<string, number>>({})
watch(turnStarts, (v) => emit('site-turns', v))
function turnBegan(id: string, at = Date.now()) {
  if (!(id in turnStarts.value)) turnStarts.value = { ...turnStarts.value, [id]: at }
}
function turnEnded(id: string) {
  if (!(id in turnStarts.value)) return
  const next = { ...turnStarts.value }
  delete next[id]
  turnStarts.value = next
}
// 现场那一格只收房间自己的事件行：分身的记在它那张卡上，消息在对话栏。
function toSite(b: Block) {
  if (b.kind === 'event' && !b.task_id) emit('site-block', b)
}

// Tool actions 芝士 performed this turn (施工现场, spec §9.1) — ephemeral.
// Working-log todo (the agent's `todo_write`). Live during a turn (§3.1.1); between
// turns it holds the topic's stored 进度层 (#187) instead of being wiped, so
// "做到哪了" is visible in the room without summoning anyone.
const todoItems = ref<TodoItem[]>([])
// The list on screen is a previous turn's leftovers, not this turn's live
// progress — labelled differently so nobody reads a stale half-circle as
// "running now".
const todoRestored = ref(false)
// 对话里只画正在跑的这一轮的清单；上一轮留下的在总览里（PanelProgress）。
const liveTodo = computed(() => todoItems.value.length > 0 && !todoRestored.value)
// 三态用图标而不是文字符号（✓ / ◐ / ○）：那三个字符的字重和基线随系统字体变，
// 在 13px 上 ◐ 和 ○ 几乎分不开。三个 mdi 图标按「填充程度」递进，一眼可分——
// 空心圈 = 还没做，半填充 = 正在做，实心圈里带勾 = 做完了。
function todoIcon(status: string): string {
  if (status === 'completed') return 'mdi-check-circle'
  if (status === 'in_progress') return 'mdi-circle-slice-4'
  return 'mdi-circle-outline'
}

// ---- 选项问题 (cheese_ask): buttons under the message; one click answers
// and summons 芝士 to continue. Answered state renders for everyone. ----
const askBusy = ref<string | null>(null)
async function pickOption(m: Block, option: string) {
  if (askBusy.value) return
  askBusy.value = m.id
  try {
    const updated = await answerOptions(m.id, option, AUTHOR)
    const bi = messages.value.findIndex((x) => x.id === m.id)
    if (bi >= 0) {
      messages.value.splice(bi, 1, updated)
      historyChanges?.set(updated.id, updated)
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '选择失败'
  } finally {
    askBusy.value = null
  }
}

// ---- Emoji reactions (Slack semantics, 协作平台的消息表情) ----
// MVP picker: a fixed strip of the 8 most common reactions.
// Which message's picker is open (one at a time).
const reactionPickerFor = ref<string | null>(null)

function applyReactions(blockId: string, reactions: ReactionAgg[]) {
  historyReactions?.set(blockId, reactions)
  const m = messages.value.find((x) => x.id === blockId)
  if (m) m.reactions = reactions
}

async function onReact(m: Block, emoji: string) {
  reactionPickerFor.value = null
  try {
    // The response carries the fresh aggregate; the `reaction` WS frame the
    // backend broadcasts is idempotent with this local apply.
    const out = await apiToggleReaction(m.id, emoji, AUTHOR)
    applyReactions(m.id, out.reactions)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '表情更新失败'
  }
}

// 「这条事件长什么样」的判断全在 lib/platformNotice.ts —— 包括动作卡认哪些块
// (refs=["action:<resource>"] / meta.action)。这里只剩按钮文案和 emit 接线。

// @mention chips are rendered via v-html; delegate clicks so the parent can
// resolve the name (person → member page, topic/doc → open it).
function onMessagesClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  // Click-away closes the emoji picker (clicks inside it are handled there).
  if (reactionPickerFor.value && !target?.closest('.rx-picker, .rx-toggle')) {
    reactionPickerFor.value = null
  }
  const el = target?.closest('.mention') as HTMLElement | null
  if (!el) return
  if (el.dataset.handle) emit('mention-click', el.dataset.handle)
  else if (el.dataset.topic) {
    const id = el.dataset.topic
    if (roomTasks.value.some((task) => task.id === id)) emit('open-card', id)
    else emit('open-topic', id)
  } else if (el.dataset.file) {
    const row = el.closest('[data-mid]') as HTMLElement | null
    const task = rows.value.find(({ block }) => block.id === row?.dataset.mid)?.block.task_id
    emit('open-file', el.dataset.file, task ?? null)
  }
}

// 滚动位置、跟不跟新消息、重放期间不抖 —— 见 room/composables/useChatScroll。
// 往回翻历史留在这里：它碰 messages / 缓存 / 错误横幅，不是滚动的事。
const {
  scrollRef,
  contentRef,
  atBottom,
  scrollToBottom,
  autoScroll,
  beginCatchUp,
  noteFrame,
  rememberScroll,
  restoreScroll,
} = useChatScroll()

// ---- 悬停条：整列一个，跟着指针在消息之间滑（见 room/RoomHoverBar）。 ----
// 指针落在一条消息上就移过去；落在行与行之间的空隙里就留在原地（从一行滑到下一行
// 的路上不该让它一闪一闪）；落在别的行上（事件、标记、「正在处理」）或移出整列
// 就收起。表情选择条开着的时候钉在那一行上，不跟指针走。
const bar = reactive({ id: null as string | null, shown: false, top: 0, jump: false })
const barBlock = computed(() => (bar.id ? messages.value.find((m) => m.id === bar.id) ?? null : null))

function rowTop(row: HTMLElement): number | null {
  const content = contentRef.value
  return content ? row.getBoundingClientRect().top - content.getBoundingClientRect().top : null
}

function showBarAt(row: HTMLElement) {
  const id = row.dataset.mid
  const top = rowTop(row)
  if (!id || top === null) return
  if (reactionPickerFor.value && reactionPickerFor.value !== id) return
  if (!bar.shown) {
    // 从收起状态出现：直接落在这一行上，只淡入，不从上一次停的地方滑过来。
    bar.jump = true
    requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
  }
  bar.id = id
  bar.top = top
  bar.shown = true
}

function hideBar() {
  if (reactionPickerFor.value) return
  bar.shown = false
}

function onTimelinePointer(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  if (!target || target.closest('.hover-bar')) return
  const row = target.closest<HTMLElement>('[data-mid], .notice-row, .room-happening, .dispatched, .tl-mark, .im-row')
  if (!row) return
  if (row.matches('[data-actions]')) showBarAt(row)
  else hideBar()
}

// 滚动事件：记下位置，顺带判断是不是滚到了要上一页的地方。
function onTimelineScroll() {
  rememberScroll(props.topic?.id)
  const el = scrollRef.value
  if (el && shouldLoadOlder(el.scrollTop, { hasMore: hasMore.value, loading: loadingOlder.value })) void loadOlder()
}

// --- paging back through history --------------------------------------------
// The panel holds a WINDOW of the timeline (newest PAGE_SIZE blocks), not the
// whole thing: a long topic was 2.1 MB / 2226 rows in one response, and the
// browser choked on all three of transfer, JSON parse, and 2226 live DOM nodes.
const hasMore = ref(false)
const loadingOlder = ref(false)

// A page of very short messages can be shorter than the pane. Then there is
// nothing to scroll, no scroll event fires, and the remaining history would be
// unreachable — so top up until the pane actually scrolls.
async function fillViewportIfNeeded() {
  await nextTick()
  const el = scrollRef.value
  if (!el || !hasMore.value || loadingOlder.value) return
  if (el.scrollHeight > el.clientHeight) return
  await loadOlder()
}

async function loadOlder() {
  const el = scrollRef.value
  const topic = props.topic
  if (!el || !topic || loadingOlder.value || !hasMore.value) return
  const oldest = messages.value[0]
  if (!oldest) return
  loadingOlder.value = true
  // Measure BEFORE the rows go in: prepending grows the content above the
  // viewport, so scrollTop has to be pushed down by exactly that much or the
  // timeline jumps out from under the reader (and re-triggers this loader).
  const before = { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight }
  let failed = false
  try {
    const payload = await listBlocks(topic.id, { limit: PAGE_SIZE, before: oldest.id })
    // The user may have switched topics while this was in flight.
    if (props.topic?.id !== topic.id) return
    const next = prependOlder({ blocks: messages.value, hasMore: hasMore.value }, payload.data, payload.has_more)
    messages.value = next.blocks
    hasMore.value = next.hasMore
    setCachedWindow(topic.id, next)
    for (const b of payload.data) older.add(b.id)
    await nextTick()
    const sc = scrollRef.value
    if (sc) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
  } catch (e) {
    failed = true
    errorMsg.value = e instanceof Error ? e.message : '加载消息失败'
  } finally {
    // Unconditional: a topic switch mid-flight must not leave the flag stuck,
    // or the new topic could never page back.
    loadingOlder.value = false
  }
  // Only now that the flag is clear can another page be pulled, if the pane
  // still isn't tall enough to scroll. Not after a failure — that would retry
  // a broken request in a tight loop.
  if (!failed) await fillViewportIfNeeded()
}

// 这条房间 socket 的连接、重连退避、心跳、换掉假活的那条 —— 见
// room/composables/useRoomSocket。它不认识帧的含义：帧交给下面的 handleFrame。
const {
  connected,
  connectRefused,
  open: openSocket,
  close: closeSocket,
  isConnectRefusal,
  retryLater,
  post: postFrame,
  replaceStale: replaceStaleSocket,
} = useRoomSocket({
  topicId: () => props.topic?.id,
  onFrame: (frame) => {
    handleFrame(frame)
    noteFrame()
  },
  onOpen: () => {
    // State frames are transient. A doc saved while disconnected may have no
    // remaining turn to replay it; refresh through the panel's conflict guard.
    emit('state-changed', 'doc')
    flushOutbox() // 断线期间打的字，连上就自己走
  },
  onDrop: () => requeueSending(),
  reconnect: (topicId) => {
    if (props.topic?.id === topicId) void loadTopic(props.topic)
  },
  errorMsg,
})

// 每次连上，broker 都会把一轮进行中的帧一次性重放出来——先进追赶模式，这一阵里
// 不逐帧滚动。
function connectSocket(topicId: string) {
  beginCatchUp()
  openSocket(topicId)
}

// Append a block unless it's already in the timeline: after a switch-away /
// return, history (DB) and the broker's in-progress-turn replay overlap, and
// a block must never show up twice (现场不能错).
let historyChanges: Map<string, Block | null> | null = null
let historyReactions: Map<string, ReactionAgg[]> | null = null
let historyGeneration = 0

// 此刻才进来的那几条消息（不是打开房间时读出来的历史）。它们进来时淡入一下：新
// 消息落在底部，这一下说的是「刚来的是这条」；读历史时演，一屏同时浮上来几十条，
// 什么也说明不了。历史快照合并完之前（`historyChanges` 还在）进来的也不算——那是
// 打开房间时的补齐。自己发的不算：发件箱那一行早已在屏幕上，换成落库的那一条时
// 再淡入一次就是一闪。
const arrived = reactive(new Set<string>())

// 出错提示停多久。一次没成的事（表情没加上、下载失败）说一句，够读完就淡出：一直
// 挂着的话它盖住输入框上方那块，而说的多半已经过去了。连不上服务器的时候不走——
// 那时候这一行说的是房间此刻的状态（连接被拒、正在重连、历史没读出来），它一走，
// 房间为什么不动就没人说了。
const ERROR_TOAST_MS = 6000
let errorTimer: ReturnType<typeof setTimeout> | undefined
watch([errorMsg, connected, connectRefused], ([message, online, refused]) => {
  clearTimeout(errorTimer)
  if (message && online && !refused) errorTimer = setTimeout(() => (errorMsg.value = null), ERROR_TOAST_MS)
})
onBeforeUnmount(() => clearTimeout(errorTimer))

// 自己刚发的那几条（发件箱里的 client id）：从输入框的方向升上来。打开房间时从草稿
// 里恢复出来的发件箱不算——那几条一直在，不是此刻发的。
const sentNow = reactive(new Set<string>())
// 发件箱那一行换成落库的那一条时，淡的那一档慢慢恢复，而不是一下跳亮。
const delivered = reactive(new Set<string>())
// 点了「编辑」的那几条：它们离开时先收拢自己的高度，原文回到输入框。别的离开（送达
// 后换成落库的那一条）必须是瞬间的，否则同一句话会在屏幕上出现两遍。
const editing = new Set<string>()

// 往上翻时拼到顶部的那一页：只淡入，不位移——这一刻滚动位置正被补偿到原处，再
// 往上浮 4px，读的人会看见整页抖一下。
const older = reactive(new Set<string>())

function pushBlock(b: Block) {
  historyChanges?.set(b.id, b)
  if (!messages.value.some((m) => m.id === b.id)) {
    if (historyChanges === null && b.author !== AUTHOR) {
      arrived.add(b.id)
      if (!atBottom.value && b.kind !== 'event') unseen.value.push(b.id)
    }
    messages.value.push(b)
  }
}

// 往上翻着的时候别人又说了话：底部浮出一颗提示，写着来了几条。点它回到最新，并让
// 来的第一条闪一下——人要找的是「新的从哪开始」，不只是「到底了」。回到底部（不管
// 是点它还是自己滚下去）它就收起。
const unseen = ref<string[]>([])
watch(atBottom, (bottom) => {
  if (bottom) unseen.value = []
})
function jumpToUnseen() {
  const first = unseen.value[0]
  const el = scrollRef.value
  if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  unseen.value = []
  if (first) flash(first)
}

// 淡入演完就把这一条从 `arrived` 里拿掉：class 一直挂着的话，它的 animation 会压住
// 之后要演的那一下（跳过来的闪一下、同类事件又来一次的亮一下）。Vue 给 scoped 的
// keyframes 名字加了后缀，所以比前缀。
function settleArrival(e: AnimationEvent, id: string) {
  if (e.animationName.startsWith('tl-arrive')) arrived.delete(id)
  if (e.animationName.startsWith('tl-older')) older.delete(id)
  if (e.animationName.startsWith('tl-delivered')) delivered.delete(id)
}
function settleSent(e: AnimationEvent, clientId: string) {
  if (e.animationName.startsWith('tl-sent')) sentNow.delete(clientId)
}
function outboxLeave(el: Element, done: () => void) {
  const clientId = (el as HTMLElement).dataset.cid
  if (clientId && editing.delete(clientId)) collapseLeave(el, done)
  else done()
}

// 一行离开时先收拢自己的高度再走，下面的东西平滑地补上来，而不是等它淡完一下子
// 跳过去（设计系统 §9.2）。
function collapseLeave(el: Element, done: () => void) {
  const row = el as HTMLElement
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    done()
  }
  row.style.height = `${row.offsetHeight}px`
  row.style.overflow = 'hidden'
  requestAnimationFrame(() => {
    row.style.height = '0'
    row.style.marginTop = '0'
    row.style.paddingTop = '0'
    row.style.paddingBottom = '0'
    row.style.opacity = '0'
  })
  row.addEventListener('transitionend', (ev) => ev.target === row && finish())
  setTimeout(finish, 400) // transitionend 不来（减弱动效、元素被提前拿走）也得收尾
}

// 跳到某一条之后，让它闪一下：滚动停下来的那一刻，眼睛要知道落在哪一行。
const flashId = ref<string | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | undefined
function flash(id: string) {
  clearTimeout(flashTimer)
  flashId.value = null
  void nextTick(() => {
    flashId.value = id
    flashTimer = setTimeout(() => (flashId.value = null), 1600)
  })
}

function handleFrame(frame: WsServerFrame) {
  switch (frame.type) {
    case 'user_block':
      if (settleOutbox(frame.block)) delivered.add(frame.block.id)
      pushBlock(frame.block)
      autoScroll()
      break
    case 'reaction':
      // 芝士的 👀 是平台落的回执：会话已经把这条消息拿进去了（后端
      // chat.confirm_prompt_receipt）。认「作者不是我自己」而不是去比对队友的
      // handle，因为名册可能还没到，那时比对不上会把指示永远卡在「正在送给」。
      // 代价是房间里有人手点 👀 会让它提早翻一下，下一轮就自己纠正。
      if (frame.reactions?.some((r) => r.emoji === '👀' && r.authors.some((a) => a !== AUTHOR)))
        reachedAgent.value = true
      // Someone toggled an emoji / 芝士's 👀 receipt landed — update the chip
      // row in place (the frame carries the block's full fresh aggregate).
      applyReactions(frame.block_id, frame.reactions)
      break
    case 'todo':
      // Working-log checklist, updated in place. `restored` marks the replay of
      // a previous turn's list at turn start (进度层) — the first live frame of
      // this turn clears the flag.
      todoItems.value = frame.items
      todoRestored.value = frame.restored === true
      autoScroll()
      break
    case 'state':
      // A platform resource changed → parent refreshes that panel live.
      // The clickable record of the action is a persisted event_block (below).
      emit('state-changed', frame.resource)
      break
    case 'event_block':
      // A persisted, clickable action card (decision/doc/...) for this turn.
      pushBlock(frame.block)
      toSite(frame.block)
      autoScroll()
      break
    case 'block_updated': {
      // 已经在时间线上的一行变了：原地换掉，不追加第二行。
      const at = messages.value.findIndex((m) => m.id === frame.block.id)
      if (at >= 0) messages.value.splice(at, 1, frame.block)
      historyChanges?.set(frame.block.id, frame.block)
      toSite(frame.block)
      break
    }
    case 'assistant_block':
      // One complete 芝士 message (Slack-style) — a turn may land several.
      pushBlock(frame.block)
      // Compatibility with an older backend that has no lifecycle markers.
      if (activeTurnIds.value.size === 0) awaitingReply.value = false
      autoScroll()
      break
    case 'error':
      if (frame.client_id) {
        if (failOutgoing(frame.client_id, frame.message)) {
          if (activeTurnIds.value.size === 0) awaitingReply.value = false
        } else {
          errorMsg.value = frame.message
        }
        return
      }
      // The socket was refused at connect — the backend closes right after this
      // frame, so latch the reason and stop the reconnect loop from burying it.
      if (isConnectRefusal(frame.code)) {
        connectRefused.value = true
        errorMsg.value = frame.message
        awaitingReply.value = false
        return
      }
      // A persisted turn failure is already in the timeline as an event block
      // (现场即事实记录); only un-persisted errors need the floating banner.
      if (!frame.persisted) errorMsg.value = frame.message
      if (activeTurnIds.value.size === 0) awaitingReply.value = false
      // A failed turn is exactly when the checklist matters most — it is what
      // whoever picks this up next (person or new machine) works from. Keep it.
      todoRestored.value = true
      break
    case 'done':
      // Mid-session messages fold into the existing Claude run and emit no
      // separate completion frame. Lifecycle markers own the running indicator.
      if (activeTurnIds.value.size === 0) {
        awaitingReply.value = false
        todoRestored.value = true
        emit('turn-done')
      }
      autoScroll()
      break
    case 'retract_block':
      historyChanges?.set(frame.block_id, null)
      messages.value = messages.value.filter((m) => m.id !== frame.block_id)
      break
    case 'agent_control':
      emit('agent-control', frame.state)
      break
    case 'turn_active':
      if (frame.turn_ids?.length) activeTurnIds.value = new Set(frame.turn_ids)
      for (const id of frame.turn_ids ?? []) {
        const since = frame.since?.[id]
        turnBegan(id, typeof since === 'number' ? since * 1000 : Date.now())
      }
      awaitingReply.value = true
      // 这个话题上有活在跑，就说明消息早到它手上了。回执是精确的那一路，这是
      // 兜底的一路：重连进来、或者会话自己开的一轮，本来就不该说「正在送给」。
      reachedAgent.value = true
      break
    case 'turn_started': {
      const next = new Set(activeTurnIds.value)
      next.add(frame.turn_id)
      activeTurnIds.value = next
      turnBegan(frame.turn_id)
      awaitingReply.value = true
      reachedAgent.value = true
      break
    }
    case 'turn_finished': {
      const next = new Set(activeTurnIds.value)
      next.delete(frame.turn_id)
      activeTurnIds.value = next
      turnEnded(frame.turn_id)
      awaitingReply.value = next.size > 0
      todoRestored.value = true
      emit('turn-done')
      autoScroll()
      break
    }
  }
}

// 卸载之后还在飞的那几个请求回来时，不该再往一个已经没了的面板上写东西。
let disposed = false

// 撤销一次自动改名（RoomNotice 那一行的按钮）。后端改完会发 `state: topics`，
// 侧栏据此重读；这里再主动报一次，按下去就能看到名字回来。
async function undoTitle(blockId: string) {
  if (!props.topic) return
  try {
    await undoTopicTitle(props.topic.id, blockId)
    emit('state-changed', 'topics')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '撤销失败'
  }
}

async function loadTopic(topic: Topic, entering = false) {
  const generation = ++historyGeneration
  const changes = new Map<string, Block | null>()
  historyChanges = changes
  const reactions = new Map<string, ReactionAgg[]>()
  historyReactions = reactions
  const stillHere = () => !disposed && generation === historyGeneration && props.topic?.id === topic.id
  errorMsg.value = null
  connectRefused.value = false // a fresh topic gets a fresh attempt at connecting
  awaitingReply.value = false
  activeTurnIds.value = new Set()
  turnStarts.value = {}
  todoItems.value = []
  todoRestored.value = false
  // 进度层 (#187): the checklist the last turn left behind. Fire-and-forget and
  // guarded on the topic still being active — it is context, never a reason to
  // hold up (or fail) opening the conversation.
  void getProgress(topic.id)
    .then((p) => {
      if (props.topic?.id !== topic.id || todoItems.value.length) return
      // `?? []` 不是防御性洁癖：这个 ref 只要被写成 undefined，模板里的
      // `todoItems.length` 就抛，整个 ChatPanel 渲染失败——房间变成白板。而下面
      // 那句 `.catch(() => {})` 只吞掉报错，撤不回已经写进去的 undefined，所以
      // 屏幕上不会有任何东西说明发生了什么。今天的后端始终带 items，够不到这里；
      // 前后端版本错开一次就够得到，代价是整个房间。
      const items = p.items ?? []
      todoItems.value = items
      todoRestored.value = items.length > 0
    })
    .catch(() => {})
  reactionPickerFor.value = null
  // 悬停条是绝对定位的：收起只是透明，它仍停在上一个话题那一行的 translateY 上，
  // 仍算进这一栏的可滚动高度。从一个翻到很深的长话题切到短话题，它把滚动区撑高，
  // 滚动位置就停在短话题的几行下面——整屏空白，刷新才好。`jump` 让它直接落回原
  // 点而不是滑回去：滑的那几百毫秒里空白照样在。
  bar.id = null
  bar.shown = false
  bar.jump = true
  bar.top = 0
  requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
  unreadAnchorId.value = null
  arrived.clear()
  older.clear()
  sentNow.clear()
  delivered.clear()
  editing.clear()
  unseen.value = []
  clearPendingAtts() // pending images belong to the topic they were typed in
  closeSocket()
  loadingOlder.value = false
  const cached = cachedWindow(topic.id)
  if (cached) {
    messages.value = cached.blocks
    hasMore.value = cached.hasMore
    restoreScroll(topic.id)
  } else {
    messages.value = []
    hasMore.value = false
    loadingHistory.value = true
  }
  try {
    // Allow composer restoration to finish, then authenticate both transports.
    // Recovery keeps its history-first reconciliation for lost message echoes.
    await ensureFreshToken()
    if (!stillHere()) return
    const parallelSocket = entering && outbox.value.length === 0
    if (parallelSocket) connectSocket(topic.id)
    // 打开话题的那次导航已经替它起了头（router/index.ts），它往往比下面这一条先
    // 回来：先回来就先画出来。不等它——那条走的是后台预取的队列，可能排在别的话题
    // 后面；下面这一条照常直接去取，谁先到用谁。
    // 先画出来的那一页之后就当缓存看待：下面合并、判断「长了没有」、要不要复位滚动，
    // 都和一开始就有缓存时一样。
    let shownEarly: typeof cached = null
    const warming = cached ? undefined : pendingBlockRefresh(topic.id)
    void warming?.then(() => {
      if (!stillHere() || !loadingHistory.value) return
      const warmed = cachedWindow(topic.id)
      if (!warmed) return
      shownEarly = warmed
      messages.value = warmed.blocks
      hasMore.value = warmed.hasMore
      loadingHistory.value = false
      restoreScroll(topic.id)
    })
    // One screenful, not the whole timeline — older blocks arrive when the
    // user scrolls up to them (loadOlder).
    const payload = await listBlocks(topic.id, { limit: PAGE_SIZE })
    // Only apply if still the active topic (avoid race on fast switching).
    if (!stillHere()) return
    // Blocks that landed while we were away append at the tail; if the user
    // was parked at the bottom, follow them so the newest message is visible
    // without a manual scroll. Compared on the LAST id, not on length: the
    // cached window and this page can be different sizes (the user may have
    // paged back), so a length comparison says nothing about the tail.
    const shown = cached ?? shownEarly
    const grew = shown !== null && shown.blocks.at(-1)?.id !== payload.data.at(-1)?.id
    // Merge rather than replace, so scrollback the user already loaded (and
    // that restoreScroll's saved offset refers to) does not vanish under them.
    const merged = shown
      ? mergeRefreshedTail(shown, { blocks: payload.data, hasMore: payload.has_more })
      : { blocks: payload.data, hasMore: payload.has_more }
    // Live frames can arrive while the HTTP snapshot is pending. Apply them
    // last, including retractions, so that snapshot cannot erase newer events.
    const blocks = new Map(merged.blocks.map((block) => [block.id, block]))
    for (const [id, block] of changes) {
      if (block) blocks.set(id, block)
      else blocks.delete(id)
    }
    for (const [id, updated] of reactions) {
      const block = blocks.get(id)
      if (block) blocks.set(id, { ...block, reactions: updated })
    }
    merged.blocks = [...blocks.values()]
    messages.value = merged.blocks
    // A reconnect starts with durable history. Settle sends that landed while
    // their echo was lost before opening the new socket; only absent client ids
    // remain queued for an idempotent resend.
    for (const block of merged.blocks) settleOutbox(block)
    hasMore.value = merged.hasMore
    setCachedWindow(topic.id, merged)
    placeUnreadAnchor() // 冻在这一刻：之后来的新消息不再移动这条线
    if (!shown) restoreScroll(topic.id)
    else if (grew && atBottom.value) autoScroll()
    if (!parallelSocket && !connectRefused.value) connectSocket(topic.id)
    void fillViewportIfNeeded()
  } catch (e) {
    if (!stillHere()) return
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) closeSocket()
    errorMsg.value = e instanceof Error ? e.message : '加载消息失败'
    // A failed history fetch must not terminate socket recovery during an outage.
    if (isRetryableGetFailure('GET', e instanceof ApiError ? e.status : undefined, e)) {
      retryLater(topic.id)
    }
  } finally {
    if (generation === historyGeneration) {
      historyChanges = null
      historyReactions = null
      loadingHistory.value = false
    }
  }
}

// Send a message. `summon` (= 这条消息 @ 了芝士) asks 芝士 to reply; when false
// the message is just posted (spec §7.1 默认不 @). The composer lives in TopicView and
// drives this via the exposed ref, so the input bar can span chat + doc.
// B3: reply target — the message this next send threads under (reply_to).
const replyTarget = ref<Block | null>(null)
function setReply(m: Block) {
  replyTarget.value = m
}
function clearReply() {
  replyTarget.value = null
}
// 输入框里那枚回复标签上写的：回复的是谁、那条说了什么。
const replyLabel = computed(() =>
  replyTarget.value
    ? t('work.room.composer.replyTo', {
        name: displayName(replyTarget.value),
        text: replySnippet(replyTarget.value, refMaps),
      })
    : null
)
function parentOf(m: Block): Block | undefined {
  return m.reply_to ? messages.value.find((x) => x.id === m.reply_to) : undefined
}
function showReplyCue(m: Block): boolean {
  // Only a person's replies are explicit threads. An AI message's reply_to is
  // the implicit link to the message that triggered it — not a thread cue.
  return isPersonBlock(m) && !!parentOf(m)
}

// An image attachment block (图片输入) — drawn in place by AttachmentImage.
// ---- 芝士摆出来的一份东西 (`cheese show` → kind=artifact) ----

// 只给下载用：downloadFile 自己会带上 Authorization。显示图片不走这里。
function imageUrl(m: Block): string {
  return props.topic ? attachmentRawUrl(props.topic.id, m.content) : ''
}
async function downloadAttachment(m: Block) {
  try {
    await downloadFile(imageUrl(m), m.content.split('/').pop() || 'file')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '下载失败'
  }
}
function scrollToMessage(id: string) {
  const el = document.querySelector(`[data-mid="${id}"]`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  flash(id)
}

// 发件箱：已经打出去、库里还没有的那几条 —— 见 room/composables/useOutbox。
// 「发出去之后房间该有什么反应」留在这里（下面的 `send`）。
const {
  outbox,
  enqueue,
  flush: flushOutbox,
  settle: settleOutbox,
  fail: failOutgoing,
  requeueSending,
  retry: retrySend,
  drop: dropSend,
  cancelTimers: cancelEchoTimers,
} = useOutbox({ post: postFrame, connected, onStale: () => replaceStaleSocket() })

function send(content: string, summon: boolean, attachments?: ChatAttachment[]): boolean {
  const trimmed = content.trim()
  const atts = attachments?.length ? attachments : undefined
  // An image-only send (no text) is a valid message (图片输入).
  if (!trimmed && !atts) return false
  errorMsg.value = null
  sentNow.add(enqueue({ content: trimmed, replyTo: replyTarget.value?.id ?? undefined, atts }))
  replyTarget.value = null
  // Only show the "awaiting reply" indicator when 芝士 was summoned — an
  // instant local ack, before anything has been delivered anywhere yet.
  if (summon) {
    awaitingReply.value = true
    reachedAgent.value = false
  }
  // The stored checklist stays on screen until this turn's first live frame
  // replaces it — blanking it here would hide 进度 during the cold start, which
  // is precisely when someone is wondering where the work got to.
  scrollToBottom()
  return true
}

defineExpose({ send, connected })

// The conversation stream shows messages + lightweight system lines only.
// doc/decision blocks are document state (they live in the doc panel), and AI
// tool/巡检 events belong in 现场 — neither belongs in the group chat (spec §7.1).
// Historical SDK turns can contain one fenced Markdown block split across
// consecutive message rows. Repair those rows before collapseNotices hides
// event blocks, because an event is a hard boundary and must prevent an
// accidental merge.
const rows = computed(() => collapseNotices(coalesceSplitFencedCodeBlocks(messages.value)))
const visible = computed<Block[]>(() => rows.value.map((r) => r.block))

// 它停着的那一行上面的东西变了（往上翻拼进来一页、上面一条长高了），行的位置跟着
// 变：重新量一次，直接落过去，不演滑动——这一下不是指针在动。
watch(
  rows,
  () =>
    void nextTick(() => {
      if (!bar.shown || !bar.id) return
      const row = scrollRef.value?.querySelector<HTMLElement>(`[data-mid="${bar.id}"]`)
      const top = row ? rowTop(row) : null
      if (top === null) return (bar.shown = false)
      if (top === bar.top) return
      bar.jump = true
      bar.top = top
      requestAnimationFrame(() => requestAnimationFrame(() => (bar.jump = false)))
    })
)
watch(reactionPickerFor, (open) => {
  if (!open && !scrollRef.value?.matches(':hover')) bar.shown = false
})

// ---- 时间刻度 ----
// 「这条属于哪一天」只在跨天时说一次。用本地日期而不是 UTC：读的人在哪个时区,
// 「今天」就该是哪个时区的今天。
const DAY_MS = 86_400_000
function dayKey(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}
function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(today) - startOf(d)) / DAY_MS)
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 7 && days > 0) return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  const sameYear = d.getFullYear() === today.getFullYear()
  return d.toLocaleDateString([], sameYear ? { month: 'long', day: 'numeric' } : undefined)
}
// blockId → 要画在它上面的那条日期线。第一条也画：翻到时间线顶部的人同样需要
// 知道这段是什么时候的。
const dayLabels = computed(() => {
  const out = new Map<string, string>()
  let prev: string | null = null
  for (const { block } of rows.value) {
    const key = dayKey(block.created_at)
    if (key !== prev) out.set(block.id, dayLabel(block.created_at))
    prev = key
  }
  return out
})

// 新消息线锚在哪条块上。开话题时按当时的未读数往回数一次就冻住 —— 它是「我上次
// 看到哪儿」的记号，不是一个会跟着新消息跑的游标。
const unreadAnchorId = ref<string | null>(null)
function placeUnreadAnchor() {
  const n = props.unreadOnOpen
  if (!n || n <= 0) return
  let seen = 0
  for (let i = rows.value.length - 1; i >= 0; i -= 1) {
    const b = rows.value[i].block
    // 和后端未读口径一致：只数别人发的消息，系统事件不算。
    if ((b.kind !== 'message' && b.kind !== 'attachment') || b.author === AUTHOR) continue
    seen += 1
    if (seen === n) {
      unreadAnchorId.value = b.id
      return
    }
  }
  // 未读比这一页还多：线就画在这一页最老的那条别人的消息上，别装作没有。
  const oldest = rows.value.find((r) => r.block.author !== AUTHOR && r.block.kind === 'message')
  unreadAnchorId.value = oldest?.block.id ?? null
}

// 「已派出」标记 (issue #314): 本房间派出去的活，在时间线上它被派出去的那个时刻
// 标一行，点进去就是那条支线。库里没有这行 —— split 不往房间主线写任何 block，所
// 以位置只能由支线的 created_at 现算（lib/splitMarkers.ts 说明了它能标什么、标不
// 了什么）。
//
// 单独拉一次而不是从 topicList 里挑：一件活不再是话题树上的一个节点，话题列表里
// 根本没有它了。`limit: 1` 是因为标记只要支线本身，不要它们的对话。
const roomTasks = ref<RoomTask[]>([])
watch(
  () => props.topic?.id,
  async (id) => {
    roomTasks.value = []
    if (!id) return
    try {
      roomTasks.value = (await listRoomTasks(id, { limit: 1 })).data
    } catch {
      // 标记是派生出来的装饰，不是内容。拉不到就少几行标记，不该让整个时间线红掉。
    }
  },
  { immediate: true }
)

// <#id> 可以指一个话题，也可以指这个房间里的一件活：两边的标题都得认得，否则活的
// chip 只会写「#话题」。
watch(
  [() => props.topicList, roomTasks],
  ([ts, tasks]) => {
    for (const k of Object.keys(topicTitles)) delete topicTitles[k]
    for (const t of ts) topicTitles[t.id] = t.title
    for (const t of tasks) topicTitles[t.id] = t.title
  },
  { immediate: true, deep: true }
)

const splitMarkers = computed(() =>
  placeSplitMarkers(roomTasks.value, {
    blocks: visible.value,
    hasMore: hasMore.value,
  })
)

function noticeAgentName(block: Block, notice: PlatformNotice): string | null {
  if (notice.mode === 'hidden' || notice.mode === 'backend-error') return null
  // This event contains the worker's actual result, rather than a status notice.
  if (block.meta?.event_type === 'subagent_stop') return null
  if (isPersonBlock(block)) return null
  // 平台替某个参与者写下的一条（「XX 编辑了文档」就是这样）：档位说「平台」，
  // 署名说是谁 —— 所以这里问的是署名，名册在手时以名册为准。
  if (isAgentHandle(block.author) || seatByHandle.value.get(block.author)?.agent) {
    return agentDisplayName(block.author)
  }
  if (seatByHandle.value.has(block.author) || memberByHandle.value.has(block.author)) return null
  if (AGENT_STATUS_EVENTS.has(String(block.meta?.event_type ?? ''))) return agentName.value
  if (block.author === 'system' && (notice.mode === 'action' || notice.mode === 'turn-summary')) {
    return agentName.value
  }
  if (block.turn_id && (block.author === 'system' || notice.mode === 'action' || notice.mode === 'turn-summary')) {
    return agentName.value
  }
  return null
}
/**
 * 发件箱那一条还没有库里的块，而消息行要的是块。补齐它需要的那几个字段：
 * `clientId` 当 id 用（重试/删除靠它认人），作者就是自己。
 */
function pendingBlock(item: Outgoing): Block {
  return { id: item.clientId, author: AUTHOR, content: item.content, kind: 'message' } as Block
}

// 时间那一格说的是送达状态。失败了就不说：失败那一行自己会说清楚是什么失败了。
function outgoingState(item: Outgoing): string {
  if (item.state === 'failed') return ''
  return connected.value ? '发送中…' : '等待连接'
}

// 发送失败之后的「编辑」：这一条从发件箱里拿掉，原文、回复对象和附件放回输入框，
// 改完再发就是一条新的。输入框里已经有字的话，原文放在前面，一个字都不覆盖。
function editSend(item: Outgoing) {
  editing.add(item.clientId)
  dropSend(item.clientId)
  draft.value = draft.value.trim() ? `${item.content}\n${draft.value}` : item.content
  const parent = item.replyTo ? messages.value.find((m) => m.id === item.replyTo) : undefined
  if (parent) replyTarget.value = parent
  if (item.atts?.length) pendingAtts.value = [...item.atts, ...pendingAtts.value]
  composerRef.value?.focus()
}

function fmtTime(iso: string): string {
  // Local HH:mm next to the name on the first of a run (not raw UTC).
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}
// 分栏 (2026-09-09, <@符露夀> 定): 我说的话靠右，别人和芝士靠左。
//
// 侧只回答一件事——**这条是不是我说的**。「谁在说」仍然由头像和名字承担，两侧
// 都保留它们：房间里是「多个人 + 一个芝士」，左边同时坐着好几个人，光靠「在左边」
// 分不出谁是谁。芝士也在左边，它是队友里的一个，不是对话的另一极。
//
// 按 handle 判，不按 author_type：author_type 只说「参与者还是平台」，而这一列
// 里有好几个人。
function isMine(m: Block): boolean {
  return isPersonBlock(m) && m.author === AUTHOR
}

// 同一个人连着说的话合并成一段：只有第一条带头像、名字和时间，其余贴在它下面。
// 断开只有三种情况，断开之后下一条重新带上名字和时间：
// - 中间隔了别的行（事件、「已派出」标记、新消息线）—— 否则它看上去像挂在那一行
//   上的续话；
// - 换了一天 —— 日期线已经横在中间；
// - 同一天里隔了一小时以上 —— 下午接着上午说的，不该读成一口气说完的。这一种
//   只空一小档（`regroup`），还是同一个人。
const REGROUP_GAP_MS = 60 * 60 * 1000
type RunEdge = 'start' | 'regroup' | 'cont'
function sameSpeaker(a: Block, b: Block): boolean {
  return a.author === b.author && a.author_type === b.author_type
}
const runEdges = computed(() => visible.value.map((_, i) => runEdge(i)))
function runEdge(i: number): RunEdge {
  if (i === 0) return 'start'
  const prev = visible.value[i - 1]
  const cur = visible.value[i]
  if (prev.kind === 'event' || cur.kind === 'event') return 'start'
  if (splitMarkers.value.before.has(cur.id) || cur.id === unreadAnchorId.value) return 'start'
  if (!sameSpeaker(prev, cur) || dayKey(prev.created_at) !== dayKey(cur.created_at)) return 'start'
  return Date.parse(cur.created_at) - Date.parse(prev.created_at) >= REGROUP_GAP_MS ? 'regroup' : 'cont'
}
// 发件箱里的那几条还没有落库时间，按「现在」算：接在自己刚说的那段后面就贴上去。
function outboxEdge(index: number): RunEdge {
  if (index > 0) return 'cont'
  const last = visible.value.at(-1)
  if (!last || last.kind === 'event' || !isMine(last) || splitMarkers.value.tail.length) return 'start'
  const now = new Date().toISOString()
  if (dayKey(last.created_at) !== dayKey(now)) return 'start'
  return Date.now() - Date.parse(last.created_at) >= REGROUP_GAP_MS ? 'regroup' : 'cont'
}

// ---- Topic header state. The labels live in lib/topicState.ts because the
// 工作台's own topic header renders the same badge — one table, so the two can
// never disagree about what `archived` is called.
const prShortId = computed(() => topicShortId(props.topic?.id))
const prState = computed(() => topicStateBadge(props.topic?.status))

// ---- Self-contained composer (only when showComposer) ----
const draft = ref('')
const composerRef = ref<{ focus: () => void } | null>(null)

const starterPrompts = [
  { label: '查找资料', text: '帮我查找相关资料，注明来源，并整理成文档。我要了解的是：' },
  { label: '起草文档', text: '帮我起草一份文档，先和我确认目标与读者。我想写的是：' },
  { label: '拆解任务', text: '帮我把目标拆成可执行的任务，先给我看分工建议。我的目标是：' },
]
// 起手区块什么时候退休：芝士在这个房间里说过第一句话之后。
//
// 退休判据**不是「房间里有东西」**。平台自己发的公告、赛题报名写进去的简报、
// 用户对着同事说的那几句，都能把房间填满，但一件都不能替代「跟芝士说上话」这
// 件事本身；照旧判据，新用户只要先说了句没 @ 的话，这个入口就没了，而他要找的
// 恰恰是「我该跟它说什么」。
//
// 只看 message / attachment：芝士也可能留下 event 行（「芝士处理中」那类），
// 那是它干活的过程，不是它对这个人开过口。
const startersRetired = computed(() =>
  visible.value.some((b) => isAgentBlock(b) && (b.kind === 'message' || b.kind === 'attachment'))
)
const showStarters = computed(
  () =>
    props.topic?.kind === 'root' &&
    props.topic.status !== 'archived' &&
    props.showComposer &&
    !loadingHistory.value &&
    !errorMsg.value &&
    !hasMore.value &&
    !startersRetired.value &&
    // 正在回话也先收起来：这一轮已经开了，芝士的答复落地之后由 `startersRetired`
    // 接手。两者中间不留一条缝——不然刚 @ 完、还没等到回话的那几秒里，起手区块
    // 会闪一下。
    !awaitingReply.value &&
    !draft.value.trim() &&
    !outbox.value.length
)

function startDraft(text: string) {
  if (draft.value.trim()) return
  // 起手草稿里那个 @ 和按钮写进去的是同一个名字（见 `agentSeat`）：写错了的话，
  // 人点完「起草文档」发出去，屋里会动的那位不动，而草稿上明明 @ 着「芝士」。
  const agent = agentSeat.value
  draft.value = `${props.alwaysSummon ? '' : `@${agent?.label ?? '芝士'} `}${text}`
  composerRef.value?.focus()
}

const {
  pending: pendingAtts,
  uploading: attsUploading,
  addFiles,
  addLibraryFile,
  onPaste: onComposerPaste,
  onDrop: onComposerDrop,
  removeAt: removePendingAtt,
  clear: clearPendingAtts,
} = usePendingAttachments(
  () => props.topic?.id,
  (msg) => {
    errorMsg.value = msg
  }
)

// 失败提示上的「重试」。只给最新的那一条：更早的失败已经被后面发生的事盖过去了，
// 在它上面重试说不清是在重试什么。房间在跑、归档了，重试都没有意义。
function canRetryAt(i: number): boolean {
  if (i !== rows.value.length - 1) return false
  if (!props.showComposer || props.topic?.status === 'archived') return false
  return !awaitingReply.value && !outbox.value.length
}
// 重试走的是「交给它」同一个入口：失败的那一轮没有把消息标成已读，所以它们还在
// 等人处理，平台重新开一轮去接。什么都没有可接的时候要说出来，不能按了没反应。
// 请求还没回来时再按一次就是再开一轮，所以按着的时候按钮是忙的。
const retryBusy = ref(false)
async function retryNow() {
  const id = props.topic?.id
  if (!id || retryBusy.value) return
  retryBusy.value = true
  try {
    const res = await summonAgent(id)
    if (res.started) awaitingReply.value = true
    else if (res.reason === 'nothing_pending') errorMsg.value = t('work.room.retry.nothingPending')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : t('work.room.retry.failed')
  } finally {
    retryBusy.value = false
  }
}

// 输入区只知道正文和「这条叫不叫它」。待发附件在这一层，因为它要跟着话题走。
function onComposerSend({ content, summon }: { content: string; summon: boolean }) {
  if (send(content, summon, uploaded(pendingAtts.value))) {
    draft.value = ''
    clearPendingAtts()
  }
}

// 每话题草稿: stash / restore everything the composer holds. Nothing here is
// "just a preference" — each field names something in the topic being left
// (a block to reply to, files already uploaded to that topic's worktree).
// 每话题草稿 (飞书语义): what you had typed, who you were replying to, and the
// images waiting to go — all belong to the topic they were composed in.
//
// 之前只有待发图片被清掉，文字和回复目标原地不动地跟着你换话题：打了一半的话
// 可能发错房间，而**回复目标**更糟——它指向的块在另一个话题里，屏幕上看不出
// 异常（本话题找不到父块就不画引用条），库里的会话树已经串了。
//
// 存在哪、分几层、谁清它，全在 lib/composerDrafts.ts —— 这里只有调用。那个 Map
// 一度住在这个文件里，于是同一个概念有两套规则，换账号只清掉了其中一套。
function rememberComposer(topicId: string) {
  // 两层各写一次，都不在这里判空——两层的「空」本来就不是同一个定义（内存那层还
  // 管着发件箱），各自判各自的。在这里判一次再分发，等于替它们决定，而那个判据
  // 只可能对其中一层是对的。
  saveComposerMemory(topicId, {
    draft: draft.value,
    reply: replyTarget.value,
    atts: uploaded(pendingAtts.value),
    outbox: outbox.value.slice(),
  })
  // 落到磁盘上的那份不含发件箱，见 lib/composerDrafts.ts 的解释。
  saveComposerDraft(topicId, {
    draft: draft.value,
    reply: replyTarget.value,
    atts: uploaded(pendingAtts.value),
  })
}

/** 落盘的那份没有发件箱（它不跨刷新，也不该跨）。 */
function asComposerDraft(stored: StoredComposerDraft | null): ComposerMemory | undefined {
  return stored ? { draft: stored.draft, reply: stored.reply, atts: stored.atts, outbox: [] } : undefined
}

function restoreComposer(topicId: string | undefined) {
  // 内存里那一份优先：它带着发件箱。只有它不在时（刚刷新过、刚开机）才回落到
  // 磁盘上那份。
  const saved = topicId ? loadComposerMemory(topicId) ?? asComposerDraft(loadComposerDraft(topicId)) : undefined
  draft.value = saved?.draft ?? ''
  replyTarget.value = saved?.reply ?? null
  pendingAtts.value = saved?.atts ?? []
  // 换话题时在飞的那些没法再等回声了（socket 换了），回到队列，等这个话题
  // 下次连上再走。它们不会在别的房间里露面。
  outbox.value = (saved?.outbox ?? []).map((o) => (o.state === 'sending' ? { ...o, state: 'queued' } : o))
}

// 边打边落盘。刷新是唯一会丢草稿的路径，而它**不会**经过 rememberComposer
// （那个跑在切话题和卸载时）——所以输入本身也要定期存一次。800ms 是打字停顿的
// 量级；localStorage 是同步的，写一次的成本就是这次停顿。
let draftSaveTimer: ReturnType<typeof setTimeout> | null = null
function flushComposer(topicId: string) {
  if (draftSaveTimer) {
    clearTimeout(draftSaveTimer)
    draftSaveTimer = null
  }
  saveComposerDraft(topicId, {
    draft: draft.value,
    reply: replyTarget.value,
    atts: uploaded(pendingAtts.value),
  })
}

watch(
  [draft, replyTarget, pendingAtts],
  () => {
    const topicId = props.topic?.id
    if (!topicId) return
    if (draftSaveTimer) clearTimeout(draftSaveTimer)
    draftSaveTimer = setTimeout(() => {
      draftSaveTimer = null
      // 停了 800ms 之后当前话题可能已经换了：那样这一笔该记在旧话题上，而旧话题
      // 走的是 rememberComposer，不差这一下。
      if (props.topic?.id === topicId) flushComposer(topicId)
    }, 800)
  },
  { deep: false }
)

// 页面被切到后台 / 关掉之前最后记一次：手机上的标签页可以被直接丢掉，不一定会
// 走 onBeforeUnmount。用的是同步写，来得及。
function flushComposerOnHide() {
  if (props.topic) flushComposer(props.topic.id)
}
useEventListener(window, 'pagehide', flushComposerOnHide)
useEventListener(document, 'visibilitychange', () => {
  if (document.visibilityState === 'hidden') flushComposerOnHide()
})

// IME (输入法) guard — see TopicView.vue for the full story: Safari fires
// compositionend BEFORE the commit-Enter keydown, which then looks like a
// plain Enter. Track composition ourselves and swallow the trailing Enter.

watch(
  () => props.topic?.id,
  (id, oldId) => {
    // Save where we were in the topic we're leaving, so coming back restores it.
    if (oldId) rememberScroll(oldId)
    if (oldId) {
      rememberComposer(oldId)
      cancelEchoTimers()
    }
    if (props.topic) {
      // loadTopic clears the pending attachments synchronously before its first
      // await, so this topic's own draft has to be restored AFTER the call.
      void loadTopic(props.topic, true)
      restoreComposer(id)
    } else {
      messages.value = []
      closeSocket()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposed = true
  // persist position across an unmount (e.g. leaving the view)
  rememberScroll(props.topic?.id)
  if (props.topic) rememberComposer(props.topic.id)
  // 链路和回声计时器由各自的 composable 在 scope 停掉时收，这里不重复一遍。
})
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
        <div>选择一个话题开始对话</div>
      </div>
    </div>

    <template v-else>
      <!-- GitHub-PR-style header (Fix 4): only for real work topics (话题 = PR).
           The root topic (本体) and private chat use the plain header below. -->
      <div v-if="!hideHeader && prHeader" class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2 flex-wrap">
          <span class="t-title">{{ topic.title }}</span>
          <span class="pr-num t-meta">#{{ prShortId }}</span>
          <v-spacer />
          <span class="pr-state ms-1" :class="prState.cls">{{ prState.label }}</span>
          <span
            class="status-dot"
            :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
            :title="connected ? '已连接' : '未连接'"
          />
        </div>
      </div>

      <!-- Plain chat header — normal chat (飞书私聊 / 本体): title + 已连接 -->
      <div v-else-if="!hideHeader" class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2">
          <v-btn
            v-if="backLabel"
            variant="text"
            size="small"
            density="comfortable"
            prepend-icon="mdi-arrow-left"
            color="medium-emphasis"
            @click="emit('back')"
          >
            {{ backLabel }}
          </v-btn>
          <span class="t-title">{{ titleOverride || topic.title }}</span>
          <v-spacer />
          <span
            class="status-dot"
            :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
            :title="connected ? '已连接' : '未连接'"
          />
        </div>
      </div>

      <!-- Message stream — Feishu group chat: left-aligned rows, grouped runs,
           per-row hover action bar, centered system/event lines. -->
      <div
        ref="scrollRef"
        class="messages flex-grow-1 overflow-y-auto py-2"
        data-testid="chat-scroll"
        @scroll="onTimelineScroll"
        @click="onMessagesClick"
        @mouseover="onTimelinePointer"
        @mouseleave="hideBar"
      >
        <!-- Single wrapper so a ResizeObserver can watch the timeline's total
             content height (rows + streaming bubble + timeline-end slot). -->
        <div ref="contentRef" class="tl-content">
          <RoomHoverBar
            :block="barBlock"
            :shown="bar.shown"
            :top="bar.top"
            :jump="bar.jump"
            :is-agent="!!barBlock && isAgentBlock(barBlock)"
            :picker-open="!!barBlock && reactionPickerFor === barBlock.id"
            @react="onReact"
            @toggle-picker="reactionPickerFor = reactionPickerFor === $event ? null : $event"
            @reply="setReply"
            @upgrade="emit('upgrade-message', $event)"
          />
          <!-- 骨架和真的那几行同形同高：到货时骨架淡出，不推动下面的东西。 -->
          <Transition name="tl-skel">
            <LoadingSkeleton v-if="loadingHistory" variant="chat" />
          </Transition>

          <section v-if="showStarters" class="chat-start px-5 py-8" aria-label="开始项目协作">
            <h2 class="t-title mb-2">从一件具体的事开始</h2>
            <p class="t-body c-muted mb-4">{{ agentName }}可以查找资料、起草文档，或和你一起拆分任务</p>
            <div class="d-flex flex-wrap ga-2">
              <v-btn
                v-for="prompt in starterPrompts"
                :key="prompt.label"
                variant="outlined"
                color="on-surface"
                size="small"
                @click="startDraft(prompt.text)"
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
            {{ loadingOlder ? '加载更早的消息…' : '更早的消息' }}
          </div>

          <template v-for="({ block: m, notice, run }, i) in rows" :key="m.id">
            <!-- 时间刻度: 换天了。一个跑几周的话题里，一串 09:32 / 14:07 分不出
               哪条是今天的——这条线是唯一说得出「那是上周」的东西。 -->
            <TimelineMark v-if="dayLabels.get(m.id)" quiet>{{ dayLabels.get(m.id) }}</TimelineMark>
            <!-- 时间刻度: 你上次离开时看到哪儿。侧栏的未读角标只回答「有没有新的」,
               这条线回答「新的从哪开始」。开话题时算一次就冻住，不随新消息移动。 -->
            <TimelineMark v-if="m.id === unreadAnchorId" tone="unread">
              <v-icon size="12">mdi-arrow-down</v-icon>
              以下是新消息
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
              :name="noticeAgentName(m, notice)"
              :time="fmtTime(notice.mode === 'agent-status' ? notice.updatedAt : m.created_at)"
              :agent-name="agentName"
              :refs="refMaps"
              :can-retry="canRetryAt(i)"
              :retrying="retryBusy"
              @animationend="settleArrival($event, m.id)"
              @open-resource="(resource, turnId) => emit('open-resource', resource, turnId)"
              @undo-title="undoTitle"
              @open-card="emit('open-card', $event)"
              @retry="retryNow"
            />
            <!-- message row -->
            <RoomMessage
              v-else-if="!notice"
              :class="{
                'tl-arrive': arrived.has(m.id),
                'tl-older': older.has(m.id),
                'tl-flash': flashId === m.id,
                'tl-delivered': delivered.has(m.id),
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
              :refs="refMaps"
              :viewer="AUTHOR"
              :active="bar.shown && bar.id === m.id"
              :ask-busy="askBusy === m.id"
              @animationend="settleArrival($event, m.id)"
              @open-file="(path, taskId) => emit('open-file', path, taskId)"
              @open-topic="emit('open-topic', $event)"
              @open-card="emit('open-card', $event)"
              @react="onReact"
              @answer="pickOption"
              @download="downloadAttachment"
              @jump="scrollToMessage"
              @avatar-error="onAvatarError"
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
          <TransitionGroup :css="false" @leave="outboxLeave">
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
              :external="isExternal(AUTHOR)"
              :avatar="avatarSrc(AUTHOR)"
              :is-agent="false"
              :time="outgoingState(item)"
              :refs="refMaps"
              :viewer="AUTHOR"
              :ask-busy="false"
              :outgoing="{ error: item.error, failed: item.state === 'failed' }"
              @animationend="settleSent($event, item.clientId)"
              @retry="retrySend(item.clientId)"
              @edit="editSend(item)"
              @avatar-error="onAvatarError"
            />
          </TransitionGroup>

          <!-- 芝士 working indicator (Slack-style: no token streaming). Shown
             from summon until every explicitly active turn finishes; the live
             working-log checklist stays visible for the whole turn. -->
          <!-- 进场上浮淡入；芝士的回复落地、这一轮结束时，它淡出，回复就在它原来的
             位置上接着往下读。 -->
          <Transition name="tl-working" @leave="collapseLeave">
            <div v-if="awaitingReply || liveTodo" class="im-row">
              <div class="im-gutter">
                <CheeseAvatar :size="28" :name="agentName" />
              </div>
              <div class="im-main">
                <div class="im-meta">
                  <span class="im-name">{{ agentName }}</span>
                </div>

                <!-- Working-log checklist (`todo_write`, §3.1.1), only while a turn is
                   live. Between turns the stored 进度层 (#187) lives in the panel's
                   总览: parked at the end of the conversation it sat under every new
                   message, pushing the talk up. 新的一项依次浮上来；状态变了图标原地
                   换、删除线淡进来。 -->
                <TransitionGroup v-if="liveTodo" tag="ul" name="todo" class="todo-list">
                  <li
                    v-for="(item, ti) in todoItems"
                    :key="item.id"
                    class="todo-item"
                    :class="'todo-' + item.status"
                    :style="{ '--i': ti }"
                  >
                    <Transition name="todo-mark" mode="out-in">
                      <v-icon :key="item.status" class="todo-mark" size="14">{{ todoIcon(item.status) }}</v-icon>
                    </Transition>
                    <span class="todo-text">{{ item.subject }}</span>
                  </li>
                </TransitionGroup>

                <!-- Instant ack before the first message / during cold start. 「正在交给」
                   换成「正在处理」时交叉淡变，不跳。 -->
                <div v-if="awaitingReply" class="im-text">
                  <Transition name="tl-swap" mode="out-in">
                    <span :key="reachedAgent ? 'working' : 'handing'" class="text-medium-emphasis">{{
                      reachedAgent ? `${agentName}正在处理…` : `正在交给${agentName}…`
                    }}</span>
                  </Transition>
                  <span class="caret" />
                </div>
              </div>
            </div>
          </Transition>

          <!-- End of the conversation timeline — GitHub PR's merge box. Host fills. -->
          <div class="px-4">
            <slot name="timeline-end" />
          </div>
        </div>
      </div>

      <!-- 往上翻着的时候来了新消息。 -->
      <div class="new-pill-anchor">
        <Transition name="new-pill">
          <button v-if="unseen.length" type="button" class="new-pill" @click="jumpToUnseen">
            <v-icon size="14">mdi-arrow-down</v-icon>
            <RollingNumber :value="unseen.length" />
            <span>{{ t('work.room.newMessages') }}</span>
          </button>
        </Transition>
      </div>

      <!-- 从底部升起，过一会儿自己淡出。新的一条直接顶替旧的，不排队：排着的旧提示
           说的多半是已经过去的事。 -->
      <Transition name="toast">
        <v-alert
          v-if="errorMsg"
          :key="errorMsg"
          type="error"
          density="compact"
          class="chat-error-toast"
          closable
          @click:close="errorMsg = null"
        >
          {{ errorMsg }}
        </v-alert>
      </Transition>

      <!-- 贴在输入框上方的那一条（验收卡）。它不随对话滚：等人做的决定要一直看得见，
           又不该每来一条消息就被推走、或者反过来把对话挤到只剩几行。 -->
      <slot name="above-composer" />

      <!-- Built-in composer (private chat / standalone use). -->
      <RoomComposer
        v-if="showComposer"
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
    </template>
  </div>
</template>

<style scoped src="./room/room-row.css"></style>

<style scoped>
.chat {
  /* Was `d-flex flex-column fill-height` on the root. Spelled here instead so
     the declarations carry normal specificity: v-show's inline `display: none`
     has to be able to win. See the comment on the root element. */
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}
.chat-error-toast {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 30;
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-2);
}
.toast-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.toast-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.toast-enter-from {
  opacity: 0;
  transform: translate(-50%, 8px);
}
.toast-leave-to {
  opacity: 0;
}
/* Working-log checklist (§3.1.1) — process, sits above the streaming text.
   Between turns the same list shows the stored 进度层 (#187) under a label. */
/* 任务清单块：一块中性底色把它和正文分开，不靠左竖条（左条纹只留给引用块和结构
   线），也不上琥珀（它是进度，不是要人去按的东西）。 */
.todo-list {
  list-style: none;
  margin: 4px 0 6px;
  padding: 6px 10px;
  background: var(--fill);
  border-radius: var(--radius-sm);
}
.todo-item {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 图标盒子没有文字基线，所以整行改成顶对齐，再把图标压到第一行文字的中线上
   ((13.6px × 1.5 − 14px) / 2 ≈ 3px)——否则多行标题会把图标顶到最后一行。 */
.todo-mark {
  flex: none;
  margin-top: 3px;
}
.todo-pending {
  color: var(--faint);
}
.todo-in_progress {
  color: var(--ink);
  font-weight: 600;
}
.todo-completed {
  color: var(--faint);
}
/* 删除线一直在，只是透明的：做完的那一刻它淡进来，不是突然出现。 */
.todo-text {
  text-decoration-line: line-through;
  text-decoration-color: transparent;
  transition:
    text-decoration-color var(--dur-base) var(--ease-standard),
    opacity var(--dur-base) var(--ease-standard);
}
.todo-item {
  transition: color var(--dur-base) var(--ease-standard);
}
.todo-completed .todo-text {
  text-decoration-color: currentcolor;
  opacity: 0.7;
}
/* 新的一项依次浮上来，一项晚 30ms。 */
.todo-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
  /* 一组不超过 300ms（§9.7）：第十项之后不再往后排。 */
  transition-delay: calc(min(var(--i, 0), 9) * 30ms);
}
.todo-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.todo-mark-enter-active {
  transition:
    opacity var(--dur-quick) var(--ease-out),
    transform var(--dur-quick) var(--ease-out);
}
.todo-mark-leave-active {
  transition:
    opacity var(--dur-press) var(--ease-in),
    transform var(--dur-press) var(--ease-in);
}
.todo-mark-enter-from,
.todo-mark-leave-to {
  opacity: 0;
  transform: scale(0.6);
}
.messages {
  background: var(--surface);
  /* A flex child's implicit min-height is its content — without this, a long
     timeline refuses to shrink and pushes whatever follows (error alert,
     reply bar) below the pane edge, clipped. */
  min-height: 0;
}

/* ---- GitHub PR header ---- */
.pr-header {
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.pr-num {
  font-weight: 400;
}
/* PR state badge — semantic for Open, muted otherwise. */
.pr-state {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}
.pr-state--open {
  /* --surface, not #fff: the ground (--ok) lightens on dark (#3FBF7F), where
     white ink drops to 2.34:1. --surface IS #fff in light, so the badge looks
     exactly as it does today, and flips to near-black ink on dark. (§1.5's
     canonical chip is --ok-ink on --ok-wash, which would also lift the light
     side above AA, but that changes how the badge looks — a call for the
     design owner, not this pass.) */
  color: var(--surface);
  background: var(--ok);
}
.pr-state--merged {
  color: var(--muted);
  background: var(--fill);
}
.pr-state--draft {
  color: var(--faint);
  background: var(--fill);
}
.pr-branch {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 6px;
  border-radius: var(--radius-sm);
  font-weight: 500;
  color: var(--muted);
}
.caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  vertical-align: text-bottom;
  margin-left: 1px;
  background: var(--ink);
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
/* 骨架到货时淡出。离场时它脱离文档流，下面已经排好的真行不会被它推一下。 */
.tl-content {
  position: relative;
}
.tl-skel-leave-active {
  position: absolute;
  inset: 0 0 auto;
  transition: opacity var(--dur-quick) var(--ease-in);
}
.tl-skel-leave-to {
  opacity: 0;
}
/* 「芝士正在处理」那一行：进来上浮淡入，这一轮结束时淡出。 */
.tl-working-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.tl-working-leave-active {
  transition:
    height var(--dur-quick) var(--ease-in),
    margin-top var(--dur-quick) var(--ease-in),
    padding var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.tl-working-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.tl-working-leave-to {
  opacity: 0;
}
.tl-swap-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.tl-swap-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.tl-swap-enter-from,
.tl-swap-leave-to {
  opacity: 0;
}
/* 新消息提示：浮在时间线底部正中，从下面升上来。 */
.new-pill-anchor {
  position: relative;
  height: 0;
}
.new-pill {
  position: absolute;
  bottom: 12px;
  left: 50%;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: rgb(var(--v-theme-on-primary)); /* 和发送键同一对：琥珀底上的字 */
  font-size: 13px;
  font-weight: 600;
  box-shadow: var(--shadow-2);
  transform: translateX(-50%);
  cursor: pointer;
}
.new-pill-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.new-pill-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.new-pill-enter-from,
.new-pill-leave-to {
  opacity: 0;
  transform: translate(-50%, 8px);
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
/* 关掉动效时光标常亮：它说的「还在往下写」靠的是在不在，不是闪不闪。 */
@media (prefers-reduced-motion: reduce) {
  .caret {
    animation: none;
  }
}
</style>
