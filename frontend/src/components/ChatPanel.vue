<script lang="ts">
// Per-topic scroll position, kept at module scope so it survives this component
// unmounting (e.g. navigating to another view) and remounting — come back to a
// topic and you land where you left off, not yanked to the bottom.
// `atBottom` is stored alongside the raw offset because "at the bottom" is a
// SEMANTIC position: the timeline's height changes between visits (blocks that
// landed while away are already in the cache, the merge box fills in async),
// so restoring a stale pixel offset would leave the newest message below the
// fold — the "last message pops in a frame late" bug.
const scrollMemory = new Map<string, { top: number; atBottom: boolean }>()
// How close to the bottom still counts as "at the bottom" (px).
const BOTTOM_THRESHOLD = 80
</script>

<script setup lang="ts">
import type {
  Block,
  ChatAttachment,
  ProjectMemberRow,
  ReactionAgg,
  TodoItem,
  Topic,
  WsClientMessage,
  WsServerFrame,
} from '../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import {
  answerOptions,
  attachmentRawUrl,
  chatWsUrl,
  getProgress,
  listBlocks,
  toggleReaction as apiToggleReaction,
} from '../api'
import { usePendingAttachments } from '../lib/attachments'
import { backendErrorPresentation } from '../lib/backendErrorEvent'
import { cachedWindow, setCachedWindow } from '../lib/blockCache'
import { mergeRefreshedTail, PAGE_SIZE, prependOlder, scrollTopAfterPrepend, shouldLoadOlder } from '../lib/blockPaging'
import { platformErrorPresentation } from '../lib/platformEvents'
import {
  coalesceSplitFencedCodeBlocks,
  renderMarkdown as renderMarkdownWith,
  renderPlain as renderPlainWith,
} from '../lib/renderMessage'
import { placeSplitMarkers } from '../lib/splitMarkers'
import { myHandle } from '../me'
import { avatarColor } from '../utils/avatar'

import CheeseAvatar from './CheeseAvatar.vue'
import DispatchedMarker from './DispatchedMarker.vue'

// Message rendering (markdown / plain / reference chips) lives in
// ../lib/renderMessage so it's unit-testable; here we just bind the
// handle→name and id→title maps filled from the roster / topics props.
const mentionNames: Record<string, string> = {}
const topicTitles: Record<string, string> = {}
const refMaps = { mentionNames, topicTitles }

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, refMaps)
}

function renderPlain(text: string): string {
  return renderPlainWith(text, refMaps)
}

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // @芝士 default for new messages. Off for human-to-human (工作台), on for
    // the 1:1 private chat where 芝士 is the only counterpart.
    defaultSummon?: boolean
    // Render a self-contained composer at the bottom (used when ChatPanel is
    // dropped in standalone, e.g. the 私聊 1:1 chat in the main area). For work
    // topics WorkspaceView keeps its own spanning composer and leaves this off.
    showComposer?: boolean
    // Show the GitHub-PR-style header (话题 = PR). Only real work topics are
    // PRs — the root topic (本体) and the 1:1 private chat are NOT, so they use
    // the plain chat header instead.
    prHeader?: boolean
    // Project roster (handle→name) so <@handle> mention tokens render as chips.
    members?: ProjectMemberRow[]
    // Project topics (id→title) so <#topicId> reference tokens render as chips.
    topicList?: Topic[]
    // Header label override for a 私聊 whose stored title is a bookkeeping key
    // (e.g. a person DM's canonical "私聊 · a · b"): show the peer's name instead.
    titleOverride?: string | null
  }>(),
  {
    defaultSummon: false,
    showComposer: false,
    prHeader: false,
    members: () => [],
    topicList: () => [],
    titleOverride: null,
  }
)

// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). `tool-used` fires per tool call
// (carries the short tool name); `turn-done` fires when a turn completes.
const emit = defineEmits<{
  (e: 'tool-used', name: string, input?: Record<string, unknown>): void
  // A cheese command changed a platform resource (doc/decision/topics/...) —
  // the parent refreshes that panel live, mid-turn.
  (e: 'state-changed', resource: string): void
  (e: 'turn-done'): void
  // ⤴ 升级为话题 (eval A1): the parent upgrades this message block into a topic.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
  // A clicked @mention chip (resolved by the parent: person → member page,
  // topic/doc → open that topic).
  (e: 'mention-click', name: string): void
  // A <&path> file chip was clicked — the parent opens it in the 文件 drawer.
  (e: 'open-file', path: string): void
  // An action card's button (decision → decisions page, milestone → calendar…).
  (e: 'open-resource', resource: string, turnId?: string): void
}>()

const AUTHOR = myHandle()

// Keep the module-level handle→name map in sync with the roster prop, so
// <@handle> tokens render with the member's display name.
watch(
  () => props.members,
  (m) => {
    for (const k of Object.keys(mentionNames)) delete mentionNames[k]
    for (const row of m) mentionNames[row.user_handle] = row.name || row.user_handle
    // 群播 tokens (fusion-design §3): <@all>/<@here> render as friendly chips,
    // not the raw literal — they are reserved handles, not roster members.
    mentionNames.all = '所有人'
    mentionNames.here = '在线成员'
  },
  { immediate: true, deep: true }
)
watch(
  () => props.topicList,
  (ts) => {
    for (const k of Object.keys(topicTitles)) delete topicTitles[k]
    for (const t of ts) topicTitles[t.id] = t.title
  },
  { immediate: true, deep: true }
)

const messages = ref<Block[]>([])
const loadingHistory = ref(false)
const connected = ref(false)
const errorMsg = ref<string | null>(null)

// Slack-style discrete messages: 芝士 doesn't stream tokens — each complete
// message lands as an `assistant_block` frame. `awaitingReply` drives the
// 正在看… indicator from summon until the FIRST message of the turn arrives.
const awaitingReply = ref(false)

// Tool actions 芝士 performed this turn (施工现场, spec §9.1) — ephemeral.
// Working-log todo (芝士's Task tools). Live during a turn (§3.1.1); between
// turns it holds the topic's stored 进度层 (#187) instead of being wiped, so
// "做到哪了" is visible in the room without summoning anyone.
const todoItems = ref<TodoItem[]>([])
// The list on screen is a previous turn's leftovers, not this turn's live
// progress — labelled differently so nobody reads a stale ◐ as "running now".
const todoRestored = ref(false)
// Action cards: 芝士's cheese actions (decision/doc/...) are persisted as system
// event blocks tagged refs=["action:<resource>"] and rendered as clickable cards.
const ACTION_META: Record<string, { verb: string; btn: string }> = {
  doc: { verb: '更新了活文档', btn: '看活文档' },
  decision: { verb: '记录了一条决策', btn: '查看决策记录' },
  topics: { verb: '更新了子话题', btn: '' },
  milestone: { verb: '钉了一个里程碑', btn: '看日历' },
  accept: { verb: '递出了验收卡', btn: '去验收' },
  notify: { verb: '发了通知', btn: '' },
}

function todoMark(status: string): string {
  return status === 'completed' ? '✓' : status === 'in_progress' ? '◐' : '○'
}

// ---- 选项问题 (cheese ask): buttons under the message; one click answers
// and summons 芝士 to continue. Answered state renders for everyone. ----
function askOptions(m: Block): string[] | null {
  const opts = (m.meta as Record<string, unknown> | null)?.options
  return Array.isArray(opts) && opts.length ? (opts as string[]) : null
}
function askAnswered(m: Block): { option: string; by: string } | null {
  const meta = m.meta as Record<string, unknown> | null
  return meta?.answered ? { option: String(meta.answered), by: String(meta.answered_by ?? '') } : null
}
const askBusy = ref<string | null>(null)
async function pickOption(m: Block, option: string) {
  if (askBusy.value) return
  askBusy.value = m.id
  try {
    const updated = await answerOptions(m.id, option, AUTHOR)
    const bi = messages.value.findIndex((x) => x.id === m.id)
    if (bi >= 0) messages.value.splice(bi, 1, updated)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '选择失败'
  } finally {
    askBusy.value = null
  }
}

// ---- Emoji reactions (Slack semantics, 协作平台的消息表情) ----
// MVP picker: a fixed strip of the 8 most common reactions.
const QUICK_EMOJIS = ['👍', '✅', '❤️', '😂', '🎉', '👀', '🙏', '➕']
// Which message's picker is open (one at a time).
const reactionPickerFor = ref<string | null>(null)

function applyReactions(blockId: string, reactions: ReactionAgg[]) {
  const m = messages.value.find((x) => x.id === blockId)
  if (m) m.reactions = reactions
}

function myReacted(r: ReactionAgg): boolean {
  return r.authors.includes(AUTHOR)
}

async function onReact(m: Block, emoji: string) {
  reactionPickerFor.value = null
  try {
    // The response carries the fresh aggregate; the `reaction` WS frame the
    // backend broadcasts is idempotent with this local apply.
    const out = await apiToggleReaction(m.id, emoji, AUTHOR)
    applyReactions(m.id, out.reactions)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '表情操作失败'
  }
}

// A system event block tagged refs=["action:<resource>"] is a clickable action
// card (decision/doc/...); returns the resource, or null for a plain event line.
function actionResource(b: Block): string | null {
  // Structured meta.action (shared events like 编辑了文档) wins; legacy
  // action:<resource> refs (cheese-only cards) still resolve.
  const metaAction = (b.meta as Record<string, unknown> | null)?.action
  if (typeof metaAction === 'string') return metaAction
  const r = (b.refs || []).find((x) => x.startsWith('action:'))
  return r ? r.slice('action:'.length) : null
}
// meta.action events carry the actor in their content (张衡/芝士 编辑了文档);
// legacy cards need the 芝士 prefix prepended.
function actionText(b: Block): string {
  const metaAction = (b.meta as Record<string, unknown> | null)?.action
  return typeof metaAction === 'string' ? b.content : `芝士${b.content}`
}

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
  else if (el.dataset.topic) emit('open-topic', el.dataset.topic)
  else if (el.dataset.file) emit('open-file', el.dataset.file)
}

// Catch-up mode: right after (re)opening the socket, the broker REPLAYS every
// buffered frame of an in-progress turn in one burst. Rendering + auto-scrolling
// per frame makes the pane visibly flash for seconds on a long turn — so during
// the burst we apply frames quietly and do ONE scroll when it goes idle.
let catchingUp = false
let catchUpTimer: ReturnType<typeof setTimeout> | null = null
function noteCatchUpFrame() {
  if (!catchingUp) return
  if (catchUpTimer) clearTimeout(catchUpTimer)
  catchUpTimer = setTimeout(() => {
    catchingUp = false
    autoScroll()
  }, 200)
}

let socket: WebSocket | null = null
const scrollRef = ref<HTMLElement | null>(null)
const contentRef = ref<HTMLElement | null>(null)

// Keep the pane glued to the bottom while the user is parked there. Timeline
// height changes AFTER the first frame of a topic switch (the merge box /
// accept card fills in async, images decode, streaming re-renders) — without
// this, the correction only came from later async scrolls (fetch completion,
// the catch-up idle timer), so the tail visibly popped in a beat late.
// ResizeObserver callbacks run after layout but before paint: the re-pin lands
// in the SAME frame as the growth, so no flash is ever painted.
let contentObserver: ResizeObserver | null = null
watch(contentRef, (el) => {
  contentObserver?.disconnect()
  contentObserver = null
  if (!el) return
  contentObserver = new ResizeObserver(() => {
    const sc = scrollRef.value
    if (!sc) return
    if (atBottom.value && !isAtBottom(sc)) sc.scrollTop = sc.scrollHeight
  })
  contentObserver.observe(el)
})

// Whether the user is parked at (or near) the bottom — drives whether incoming
// messages auto-follow or leave the user's scroll position alone.
const atBottom = ref(true)

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) {
      el.scrollTop = el.scrollHeight
      atBottom.value = true
    }
  })
}

// Auto-follow new messages only when the user hasn't scrolled up. During a
// replay catch-up the per-frame calls are suppressed; noteCatchUpFrame does a
// single scroll once the burst settles.
function autoScroll() {
  if (catchingUp) return
  if (atBottom.value) scrollToBottom()
}

// Save the current scroll position for the active topic (called on scroll).
function rememberScroll() {
  const el = scrollRef.value
  if (!el || !props.topic) return
  atBottom.value = isAtBottom(el)
  scrollMemory.set(props.topic.id, { top: el.scrollTop, atBottom: atBottom.value })
  // Scrolling near the top is the request for the previous page.
  if (shouldLoadOlder(el.scrollTop, { hasMore: hasMore.value, loading: loadingOlder.value })) {
    void loadOlder()
  }
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
    await nextTick()
    const sc = scrollRef.value
    if (sc) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
  } catch (e) {
    failed = true
    errorMsg.value = e instanceof Error ? e.message : '加载更早的消息失败'
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

// Restore a topic's saved scroll position. "At the bottom" (and no memory at
// all) restores to the CURRENT bottom rather than the remembered offset — the
// timeline may be taller than when we left (background cache refresh already
// holds the messages that landed while away), and the newest message must be
// visible on the very first frame.
function restoreScroll(topicId: string) {
  nextTick(() => {
    const el = scrollRef.value
    if (!el) return
    const saved = scrollMemory.get(topicId)
    if (saved && !saved.atBottom) {
      el.scrollTop = saved.top
      atBottom.value = isAtBottom(el)
    } else {
      el.scrollTop = el.scrollHeight
      atBottom.value = true
    }
  })
}

// Auto-reconnect (协作软件语义): a backend deploy/restart must be a blip, not a
// frozen pane needing a manual refresh. An UNEXPECTED close schedules a
// reconnect with backoff; every deliberate teardown funnels through
// closeSocket(), which cancels it. The reconnect re-runs loadTopic so history
// gaps from the outage are refetched (pushBlock dedups the overlap).
let retryTimer: ReturnType<typeof setTimeout> | null = null
let retryDelayMs = 1000

function cancelRetry() {
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
}

// Every way the backend can refuse a socket AT CONNECT (app/api/routes/chat.py):
// no token, a token it could not verify, and a verified token whose owner is not
// on this topic's roster. The set is the point — `forbidden` was left out once
// and behaved exactly like the bug this latch exists to fix, because a refusal
// the client doesn't recognise falls through to the reconnect path below.
const CONNECT_REFUSAL_CODES = new Set(['auth_required', 'auth_expired', 'forbidden'])

// A connect refusal is not an outage: the backend closes the socket after one
// error frame, so retrying just reopens and gets refused again. And it does not
// even back off — the HANDSHAKE succeeds, the refusal arrives as a frame, so
// onopen has already cleared the banner and reset retryDelayMs to 1s before the
// reason lands. Measured with `forbidden` unlatched: 9 connections in 8 seconds,
// the green dot flickering and the reason blinking with it, forever. So we latch
// it: stop retrying and keep the reason on screen until they act.
const connectRefused = ref(false)

function scheduleReconnect(topicId: string) {
  if (retryTimer || connectRefused.value) return
  const delay = retryDelayMs
  retryDelayMs = Math.min(retryDelayMs * 2, 15000)
  retryTimer = setTimeout(() => {
    retryTimer = null
    // Only if the user is still on this topic (switching cancels via closeSocket,
    // but double-check against races).
    if (props.topic?.id === topicId) void loadTopic(props.topic)
  }, delay)
}

function closeSocket() {
  cancelRetry()
  if (socket) {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    socket.close()
    socket = null
  }
  connected.value = false
}

function openSocket(topicId: string) {
  catchingUp = true
  noteCatchUpFrame()
  closeSocket()
  const ws = new WebSocket(chatWsUrl(topicId))
  socket = ws

  ws.onopen = () => {
    connected.value = true
    retryDelayMs = 1000 // healthy again → next outage starts backoff fresh
    errorMsg.value = null
  }
  ws.onclose = () => {
    if (socket === ws) {
      connected.value = false
      scheduleReconnect(topicId)
    }
  }
  ws.onerror = () => {
    // The close handler owns retry; the banner just explains the grey dot.
    if (!connectRefused.value) errorMsg.value = '连接断开，正在自动重连…'
  }
  ws.onmessage = (ev: MessageEvent) => {
    // Guard against frames from a stale socket after topic switch.
    if (socket !== ws) return
    let frame: WsServerFrame
    try {
      frame = JSON.parse(ev.data as string) as WsServerFrame
    } catch {
      return
    }
    handleFrame(frame)
    noteCatchUpFrame()
  }
}

// Append a block unless it's already in the timeline: after a switch-away /
// return, history (DB) and the broker's in-progress-turn replay overlap, and
// a block must never show up twice (现场不能错).
function pushBlock(b: Block) {
  if (!messages.value.some((m) => m.id === b.id)) {
    messages.value.push(b)
  }
}

function handleFrame(frame: WsServerFrame) {
  switch (frame.type) {
    case 'user_block':
      pushBlock(frame.block)
      autoScroll()
      break
    case 'reaction':
      // Someone toggled an emoji / 芝士's ✅ receipt landed — update the chip
      // row in place (the frame carries the block's full fresh aggregate).
      applyReactions(frame.block_id, frame.reactions)
      break
    case 'tool':
      // 工作细节不进对话流 — the live feed belongs to the 现场 drawer. Hand
      // the parent the full call so it can build the live worklog line.
      emit('tool-used', frame.name.replace(/^mcp__cheese__/, ''), frame.input)
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
      // cheese changed a platform resource → parent refreshes that panel live.
      // The clickable record of the action is a persisted event_block (below).
      emit('state-changed', frame.resource)
      break
    case 'event_block':
      // A persisted, clickable action card (decision/doc/...) for this turn.
      pushBlock(frame.block)
      autoScroll()
      break
    case 'assistant_block':
      // One complete 芝士 message (Slack-style) — a turn may land several.
      // The first one retires the 正在看… indicator; the working-log todo
      // stays visible until the turn actually finishes.
      pushBlock(frame.block)
      awaitingReply.value = false
      autoScroll()
      break
    case 'error':
      // The socket was refused at connect — the backend closes right after this
      // frame, so latch the reason and stop the reconnect loop from burying it.
      if (frame.code && CONNECT_REFUSAL_CODES.has(frame.code)) {
        connectRefused.value = true
        errorMsg.value = frame.message
        awaitingReply.value = false
        return
      }
      // A persisted turn failure is already in the timeline as an event block
      // (现场即事实记录); only un-persisted errors need the floating banner.
      if (!frame.persisted) errorMsg.value = frame.message
      awaitingReply.value = false
      // A failed turn is exactly when the checklist matters most — it is what
      // whoever picks this up next (person or new machine) works from. Keep it.
      todoRestored.value = true
      break
    case 'done':
      awaitingReply.value = false
      // Kept, not cleared: the checklist is the topic's 进度层 now, not just
      // this turn's working log, and it is what the next turn resumes from.
      todoRestored.value = true
      emit('turn-done')
      autoScroll()
      break
    case 'retract_block':
      messages.value = messages.value.filter((m) => m.id !== frame.block_id)
      break
    case 'turn_active':
      // Re-entered a topic whose turn is mid-stream: show 正在思考 until the
      // replayed/live frames take over (cleared by assistant_block/done).
      awaitingReply.value = true
      break
  }
}

async function loadTopic(topic: Topic) {
  errorMsg.value = null
  connectRefused.value = false // a fresh topic gets a fresh attempt at connecting
  awaitingReply.value = false
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
    // One screenful, not the whole timeline — older blocks arrive when the
    // user scrolls up to them (loadOlder).
    const payload = await listBlocks(topic.id, { limit: PAGE_SIZE })
    // Only apply if still the active topic (avoid race on fast switching).
    if (props.topic?.id !== topic.id) return
    // Blocks that landed while we were away append at the tail; if the user
    // was parked at the bottom, follow them so the newest message is visible
    // without a manual scroll. Compared on the LAST id, not on length: the
    // cached window and this page can be different sizes (the user may have
    // paged back), so a length comparison says nothing about the tail.
    const grew = cached !== null && cached.blocks.at(-1)?.id !== payload.data.at(-1)?.id
    // Merge rather than replace, so scrollback the user already loaded (and
    // that restoreScroll's saved offset refers to) does not vanish under them.
    const merged = cached
      ? mergeRefreshedTail(cached, { blocks: payload.data, hasMore: payload.has_more })
      : { blocks: payload.data, hasMore: payload.has_more }
    messages.value = merged.blocks
    hasMore.value = merged.hasMore
    setCachedWindow(topic.id, merged)
    if (!cached) restoreScroll(topic.id)
    else if (grew && atBottom.value) autoScroll()
    openSocket(topic.id)
    void fillViewportIfNeeded()
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载历史失败'
  } finally {
    if (props.topic?.id === topic.id) loadingHistory.value = false
  }
}

// Send a message. `summon` (= @芝士) asks 芝士 to reply; when false the message
// is just posted (spec §7.1 默认不 @). The composer lives in WorkspaceView and
// drives this via the exposed ref, so the input bar can span chat + doc.
// B3: reply target — the message this next send threads under (reply_to).
const replyTarget = ref<Block | null>(null)
function setReply(m: Block) {
  replyTarget.value = m
}
function clearReply() {
  replyTarget.value = null
}
function parentOf(m: Block): Block | undefined {
  return m.reply_to ? messages.value.find((x) => x.id === m.reply_to) : undefined
}
function showReplyCue(m: Block): boolean {
  // Only human replies are explicit threads. An AI message's reply_to is the
  // implicit link to the user message that triggered it — not a thread cue.
  return m.author_type === 'human' && !!parentOf(m)
}
function replySnippet(m: Block): string {
  if (m.kind === 'attachment') return '[图片]'
  const t = m.content.replace(/\s+/g, ' ').trim()
  return t.length > 24 ? t.slice(0, 24) + '…' : t
}

// An image attachment block (图片输入) — rendered as an inline <img>.
function isImageBlock(m: Block): boolean {
  return m.kind === 'attachment' && (m.mime_type || '').startsWith('image/')
}
function imageUrl(m: Block): string {
  return props.topic ? attachmentRawUrl(props.topic.id, m.content) : ''
}
function scrollToMessage(id: string) {
  document.querySelector(`[data-mid="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function send(content: string, summon: boolean, attachments?: ChatAttachment[]): boolean {
  const trimmed = content.trim()
  const atts = attachments?.length ? attachments : undefined
  // An image-only send (no text) is a valid message (图片输入).
  if ((!trimmed && !atts) || !socket || socket.readyState !== WebSocket.OPEN) return false
  errorMsg.value = null
  const msg: WsClientMessage = {
    type: 'message',
    content: trimmed,
    summon,
    reply_to: replyTarget.value?.id ?? undefined,
    attachments: atts,
  }
  replyTarget.value = null
  socket.send(JSON.stringify(msg))
  // Only show the "awaiting reply" indicator when 芝士 was summoned — an
  // instant local ack (正在看…) even before the backend's ✅ receipt lands.
  if (summon) awaitingReply.value = true
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
const visible = computed<Block[]>(() => {
  const out: Block[] = []
  // Historical SDK turns can contain one fenced Markdown block split across
  // consecutive message rows. Repair those rows before hiding event blocks,
  // because an event is a hard boundary and must prevent an accidental merge.
  for (const m of coalesceSplitFencedCodeBlocks(messages.value)) {
    if (m.kind === 'message' || m.kind === 'attachment') {
      out.push(m)
    } else if (m.kind === 'event' && m.author_type === 'system') {
      // 前端报错 events belong to the 现场 drawer (debugging surface), not the
      // group chat — same rule as tool events (frontend_log.py).
      if ((m.meta as Record<string, unknown> | null)?.event_type === 'frontend_error') {
        continue
      }
      // Collapse a run of identical system lines (e.g. repeated 编辑了文档) so
      // a burst of edits shows as one line, not a wall.
      const prev = out[out.length - 1]
      if (prev && prev.kind === 'event' && prev.author_type === 'system' && prev.content === m.content) {
        continue
      }
      out.push(m)
    }
  }
  return out
})

// 「已派出」标记 (issue #314): 本房间拆出去的子话题，在时间线上它被拆出去的那个
// 时刻标一行，点进去就是那边。库里没有这行 —— split 不往父话题写任何 block，所以
// 位置只能由子话题的 parent_id + created_at 现算（lib/splitMarkers.ts 说明了它能
// 标什么、标不了什么）。topicList 是本项目的全部话题，子话题已经在里面了。
const splitMarkers = computed(() =>
  placeSplitMarkers(props.topic?.id, props.topicList, {
    blocks: visible.value,
    hasMore: hasMore.value,
  })
)

// ---- Feishu group-chat helpers (Fix 2) ----
function displayName(m: Block): string {
  return m.author_type === 'ai' ? '芝士' : m.author
}
function fmtTime(iso: string): string {
  // Local HH:mm next to the name on the first of a run (not raw UTC).
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}
// Group consecutive messages from the same author into runs: only the first of
// a run shows the avatar + name + time; the rest indent under the text column.
// An event block always breaks a run so the next message keeps its header.
function isRunStart(i: number): boolean {
  if (i === 0) return true
  const prev = visible.value[i - 1]
  const cur = visible.value[i]
  if (prev.kind === 'event' || cur.kind === 'event') return true
  // 一条「已派出」标记横在中间时，下面这条必须重新带头像和名字 —— 否则它看上去
  // 像是挂在标记上的续话。同 event 的道理：中间隔了东西，run 就断了。
  if (splitMarkers.value.before.has(cur.id)) return true
  return prev.author !== cur.author || prev.author_type !== cur.author_type
}

// ---- Topic header state — product language, not git's (去 PR 化). The
// branch/merge machinery is real underneath, but a normal user shouldn't
// need to read git to know where a topic stands.
const prShortId = computed(() => props.topic?.id.slice(0, 6) ?? '')
const prState = computed(() => {
  const s = props.topic?.status
  if (s === 'archived') return { label: '已采纳', cls: 'pr-state--merged' }
  if (s === 'draft') return { label: '草稿', cls: 'pr-state--draft' }
  return { label: '进行中', cls: 'pr-state--open' }
})

// ---- Self-contained composer (only when showComposer) ----
const draft = ref('')
const summon = ref(props.defaultSummon)
const composerInput = ref<{ focus?: () => void } | null>(null)

// 同 WorkspaceView：切换后把焦点还给输入框，否则 chip 一直握着焦点，用户接下来
// 按的那次 Enter 打在 chip 上，把刚点亮的 @芝士 又静默关掉且不发送。
function toggleSummon() {
  summon.value = !summon.value
  void nextTick(() => composerInput.value?.focus?.())
}

// @-autocomplete (§3.1.1 人也能 @): the @token being typed at the end of the
// draft, and the teammates / topics / broadcast tokens it can complete to.
// Mirrors WorkspaceView's composer so the root-topic and 私聊 composers get the
// same picker.
const mentionQuery = computed(() => {
  const m = draft.value.match(/@([^\s@]*)$/)
  return m ? m[1] : null
})
interface MentionItem {
  label: string
  kind: 'member' | 'topic' | 'broadcast'
  // Text written after the "@" when picked (a handle/name/token).
  insert: string
  // Secondary line: @handle for people, status for topics, hint for broadcast.
  sub: string
  agent: boolean
}
// 群播 (fusion-design §3): @all/@here are FIXED-LITERAL tokens (rule 4), pinned
// at the top. expandMentions turns them into <@all>/<@here>.
const BROADCAST_ITEMS: MentionItem[] = [
  { label: '所有人', kind: 'broadcast', insert: 'all', sub: '@all · 通知话题全体成员', agent: false },
  { label: '在线成员', kind: 'broadcast', insert: 'here', sub: '@here · 通知在线成员', agent: false },
]
const mentionMatches = computed<MentionItem[]>(() => {
  const q = mentionQuery.value
  if (q === null) return []
  const ql = q.toLowerCase()
  const broadcast = BROADCAST_ITEMS.filter((b) => b.insert.startsWith(ql) || b.label.includes(q))
  const rest: MentionItem[] = [
    ...props.members.map((m) => ({
      label: m.name || m.user_handle,
      kind: 'member' as const,
      insert: m.name || m.user_handle,
      sub: `@${m.user_handle}`,
      agent: !!m.agent,
    })),
    ...props.topicList
      .filter((t) => t.kind !== 'root')
      .map((t) => ({
        label: t.title,
        kind: 'topic' as const,
        insert: t.title,
        sub: t.status === 'archived' ? '已归档' : '进行中',
        agent: false,
      })),
  ].filter((i) => i.label.toLowerCase().includes(ql))
  return [...broadcast, ...rest].slice(0, 7)
})
function pickMention(item: MentionItem) {
  draft.value = draft.value.replace(/@([^\s@]*)$/, `@${item.insert} `)
}

// Human composer: turn a friendly "@名字 / @话题名 / @handle" into the canonical
// token (<@handle> / <#topicId>) at send time — longest patterns first so
// substrings don't mis-match. The backend re-canonicalizes as a backstop, so a
// name typed without picking from the menu still resolves.
function expandMentions(text: string): string {
  const subs: { pat: string; token: string }[] = [
    { pat: '@all', token: '<@all>' },
    { pat: '@here', token: '<@here>' },
    ...props.members.flatMap((m) => [
      { pat: `@${m.name || m.user_handle}`, token: `<@${m.user_handle}>` },
      { pat: `@${m.user_handle}`, token: `<@${m.user_handle}>` },
    ]),
    ...props.topicList.filter((t) => t.kind !== 'root').map((t) => ({ pat: `@${t.title}`, token: `<#${t.id}>` })),
  ].sort((a, b) => b.pat.length - a.pat.length)
  let out = text
  for (const s of subs) out = out.split(s.pat).join(s.token)
  return out
}

// 图片输入: paste (screenshot) or pick images; they upload to the topic's
// worktree immediately and wait in a preview strip until send.
const fileInput = ref<HTMLInputElement | null>(null)
const {
  pending: pendingAtts,
  uploading: attsUploading,
  addFiles,
  onPaste: onComposerPaste,
  removeAt: removePendingAtt,
  clear: clearPendingAtts,
} = usePendingAttachments(
  () => props.topic?.id,
  (msg) => {
    errorMsg.value = msg
  }
)
function pickFiles() {
  fileInput.value?.click()
}
function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void addFiles(Array.from(input.files))
  input.value = '' // allow re-picking the same file
}

function sendDraft() {
  if (send(expandMentions(draft.value), summon.value, pendingAtts.value.slice())) {
    draft.value = ''
    clearPendingAtts()
  }
}

// IME (输入法) guard — see WorkspaceView.vue for the full story: Safari fires
// compositionend BEFORE the commit-Enter keydown, which then looks like a
// plain Enter. Track composition ourselves and swallow the trailing Enter.
let composing = false
let compositionEndedAt = -1e9
function onCompositionStart() {
  composing = true
}
function onCompositionEnd(e: CompositionEvent) {
  composing = false
  compositionEndedAt = e.timeStamp
}
function isImeKey(e: KeyboardEvent) {
  return composing || e.isComposing || e.keyCode === 229 || e.timeStamp - compositionEndedAt < 100
}

function onComposerKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey) return
  // IME composition (拼音选字/上屏) 的回车是按给输入法的，绝不当成发送。
  if (isImeKey(e)) return
  // Only act on Enter from the focused composer textarea itself.
  const t = e.target as HTMLElement | null
  if (!t || t.tagName !== 'TEXTAREA' || document.activeElement !== t) return
  e.preventDefault()
  // While the @-menu is open, Enter picks the first match instead of sending.
  if (mentionMatches.value.length) {
    pickMention(mentionMatches.value[0])
    return
  }
  sendDraft()
}

// Keyed on the topic's id, NOT the object reference: the parent replaces
// `topics.value` wholesale on every refreshTopics() (e.g. after each agent
// turn), which mints a brand-new object for the SAME topic. Watching the
// object itself made every turn look like a topic switch — full reconnect,
// history reload, composer disabled mid-reconnect (which blurs it). Only a
// real id change is a real switch.
watch(
  () => props.topic?.id,
  (id, oldId) => {
    // Save where we were in the topic we're leaving, so coming back restores it.
    if (oldId && scrollRef.value) {
      const el = scrollRef.value
      scrollMemory.set(oldId, { top: el.scrollTop, atBottom: isAtBottom(el) })
    }
    if (props.topic) loadTopic(props.topic)
    else {
      messages.value = []
      closeSocket()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  rememberScroll() // persist position across an unmount (e.g. leaving the view)
  contentObserver?.disconnect()
  contentObserver = null
  closeSocket()
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
        <div>选择或新建一个话题，开始对话</div>
      </div>
    </div>

    <template v-else>
      <!-- GitHub-PR-style header (Fix 4): only for real work topics (话题 = PR).
           The root topic (本体) and private chat use the plain header below. -->
      <div v-if="prHeader" class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2 flex-wrap">
          <span class="pr-title t-title">{{ topic.title }}</span>
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
      <div v-else class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2">
          <span class="pr-title t-title">{{ titleOverride || topic.title }}</span>
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
        @scroll="rememberScroll"
        @click="onMessagesClick"
      >
        <!-- Single wrapper so a ResizeObserver can watch the timeline's total
             content height (rows + streaming bubble + timeline-end slot). -->
        <div ref="contentRef">
          <div v-if="loadingHistory" class="text-medium-emphasis text-body-2 px-4 py-2">加载历史…</div>

          <!-- Paging back through history. The row is always rendered while
               older blocks exist so the timeline's top edge does not change
               height when a fetch starts — that height change would move the
               reader mid-scroll, which is the very thing loadOlder compensates
               for. -->
          <div
            v-else-if="hasMore"
            class="text-medium-emphasis text-body-2 px-4 py-2 text-center"
            data-testid="chat-older-loader"
          >
            {{ loadingOlder ? '加载更早的消息…' : '向上滚动查看更早的消息' }}
          </div>

          <template v-for="(m, i) in visible" :key="m.id">
            <!-- 「已派出」标记 (issue #314): 拆出子话题在库里不留任何 block，所以
               这一行是按子话题的 parent_id + created_at 现算出来的，插在它被拆出
               去的那个时刻上。它不是消息，但会像 event 一样把消息分组打断。 -->
            <DispatchedMarker
              v-for="marker in splitMarkers.before.get(m.id) ?? []"
              :key="marker.topicId"
              :marker="marker"
              @open="emit('open-topic', $event)"
            />
            <!-- Infrastructure incidents are facts in the conversation, but they
               are neither 芝士 messages nor faint activity lines. Structured
               metadata selects this persistent, accessible recovery card. -->
            <div
              v-if="platformErrorPresentation(m)"
              class="platform-incident"
              role="alert"
              :data-error-code="platformErrorPresentation(m)!.code"
              data-testid="platform-error-card"
            >
              <div class="platform-incident__icon" aria-hidden="true">
                <v-icon :icon="platformErrorPresentation(m)!.icon" size="20" />
              </div>
              <div class="platform-incident__content">
                <div class="platform-incident__eyebrow">平台资源</div>
                <div class="platform-incident__title">
                  {{ platformErrorPresentation(m)!.title }}
                </div>
                <div class="platform-incident__body">
                  {{ platformErrorPresentation(m)!.body }}
                </div>
                <div class="platform-incident__status">
                  <span class="platform-incident__pulse" aria-hidden="true" />
                  {{ platformErrorPresentation(m)!.status }}
                </div>
              </div>
            </div>
            <!-- action row: 芝士's cheese action this turn — a quiet system line
               (amber dot = platform act) with an inline amber link, no box. -->
            <div v-else-if="m.kind === 'event' && actionResource(m)" class="action-card">
              <!-- actionText may carry a <@handle> actor token (编辑了文档): render
                 through the shared token→chip path so the actor is clickable. -->
              <span class="action-verb" v-html="renderPlain(actionText(m))" />
              <button
                v-if="ACTION_META[actionResource(m)!]?.btn"
                type="button"
                class="action-link"
                @click="emit('open-resource', actionResource(m)!, m.turn_id ?? undefined)"
              >
                {{ ACTION_META[actionResource(m)!].btn }}
              </button>
            </div>
            <!-- 后端报错 (backend_log.py): 芝士 needs the whole traceback, a
               person needs to know it happened. So the line shows by default
               and the stack is one click away — a room is a conversation, not
               a monitoring dashboard. -->
            <details v-else-if="backendErrorPresentation(m)" class="backend-error" data-testid="backend-error-event">
              <summary class="backend-error__line">
                <span>{{ backendErrorPresentation(m)!.line }}</span>
                <span v-if="backendErrorPresentation(m)!.count" class="backend-error__count">
                  ×{{ backendErrorPresentation(m)!.count }}
                </span>
              </summary>
              <div class="backend-error__meta">
                <span v-if="backendErrorPresentation(m)!.where">{{ backendErrorPresentation(m)!.where }}</span>
                <span v-if="backendErrorPresentation(m)!.requestId">
                  req {{ backendErrorPresentation(m)!.requestId }}
                </span>
              </div>
              <pre v-if="backendErrorPresentation(m)!.stack" class="backend-error__stack">{{
                backendErrorPresentation(m)!.stack
              }}</pre>
            </details>
            <!-- system / event blocks: centered, gray, small (Feishu 系统提示).
               Content may carry a <@handle> actor token (归档/编辑…): render it
               through the SAME token→chip path as messages so the actor is a
               clickable mention, not raw text. -->
            <div v-else-if="m.kind === 'event'" class="im-event text-caption">
              <span v-html="renderPlain(m.content)" />
            </div>

            <!-- message row -->
            <div v-else class="im-row" :class="{ 'im-row--cont': !isRunStart(i) }" :data-mid="m.id">
              <!-- avatar gutter: only on the first of a run -->
              <div class="im-gutter">
                <template v-if="isRunStart(i)">
                  <CheeseAvatar v-if="m.author_type === 'ai'" :size="28" />
                  <div v-else class="im-avatar" :style="{ backgroundColor: avatarColor(m.author) }">
                    {{ m.author.slice(0, 1).toUpperCase() }}
                  </div>
                </template>
              </div>

              <div class="im-main">
                <div v-if="isRunStart(i)" class="im-meta">
                  <span class="im-name">{{ displayName(m) }}</span>
                  <span class="im-time">{{ fmtTime(m.created_at) }}</span>
                </div>
                <!-- B3: a reply shows the message it threads under -->
                <button v-if="showReplyCue(m)" type="button" class="im-replied" @click="scrollToMessage(m.reply_to!)">
                  <v-icon size="12">mdi-reply</v-icon>
                  回复 {{ displayName(parentOf(m)!) }}：{{ replySnippet(parentOf(m)!) }}
                </button>
                <!-- 图片输入: an attachment block renders as the image itself
                   (click opens the original in a new tab). -->
                <a v-if="isImageBlock(m)" class="im-image-link" :href="imageUrl(m)" target="_blank" rel="noopener">
                  <img class="im-image" :src="imageUrl(m)" :alt="m.content" loading="lazy" />
                </a>
                <div v-else-if="m.author_type === 'ai'" class="im-text md-content" v-html="renderMarkdown(m.content)" />
                <!-- 现场尊重原文: human text renders verbatim — newlines and
                   spacing preserved (pre-wrap), no markdown reflow. -->
                <div v-else class="im-text im-text--verbatim" v-html="renderPlain(m.content)" />
                <!-- 选项问题 (cheese ask): one-click answer buttons; answered
                   state shows the pick + who made it (everyone sees it). -->
                <div v-if="askOptions(m)" class="ask-row">
                  <template v-if="!askAnswered(m)">
                    <button
                      v-for="opt in askOptions(m)"
                      :key="opt"
                      type="button"
                      class="ask-option"
                      :disabled="askBusy === m.id"
                      @click="pickOption(m, opt)"
                    >
                      {{ opt }}
                    </button>
                  </template>
                  <div v-else class="ask-answered">
                    <v-icon size="13" color="primary">mdi-check-circle</v-icon>
                    {{ askAnswered(m)!.by }} 选了「{{ askAnswered(m)!.option }}」
                  </div>
                </div>
                <!-- 活引用 (eval A1): an upgraded block links to its new topic. -->
                <button
                  v-if="m.upgraded_to_topic_id"
                  type="button"
                  class="im-upgraded"
                  @click="emit('open-topic', m.upgraded_to_topic_id)"
                >
                  <v-icon size="13">mdi-arrow-top-right</v-icon>
                  已升级为话题，点击查看
                </button>
                <!-- Emoji reaction chips (Slack): count per emoji, own reactions
                   highlighted; click toggles. 芝士's ✅ receipt lands here too. -->
                <div v-if="m.reactions?.length" class="rx-row">
                  <button
                    v-for="r in m.reactions"
                    :key="r.emoji"
                    type="button"
                    class="rx-chip"
                    :class="{ 'rx-chip--mine': myReacted(r) }"
                    :title="r.authors.join('、')"
                    @click="onReact(m, r.emoji)"
                  >
                    <span class="rx-emoji">{{ r.emoji }}</span>
                    <span class="rx-count">{{ r.count }}</span>
                  </button>
                </div>
              </div>

              <!-- hover action bar, top-right of the row (Feishu). Only actions
                 we actually implement are shown (no dead buttons). -->
              <div class="im-actions" :class="{ 'im-actions--open': reactionPickerFor === m.id }">
                <button
                  type="button"
                  class="im-act rx-toggle"
                  :class="{ 'im-act--on': reactionPickerFor === m.id }"
                  title="加表情"
                  @click="reactionPickerFor = reactionPickerFor === m.id ? null : m.id"
                >
                  <v-icon size="15">mdi-emoticon-happy-outline</v-icon>
                </button>
                <button type="button" class="im-act" title="回复" @click="setReply(m)">
                  <v-icon size="15">mdi-reply-outline</v-icon>
                </button>
                <button type="button" class="im-act" title="升级为话题" @click="emit('upgrade-message', m.id)">
                  <v-icon size="15">mdi-comment-arrow-right-outline</v-icon>
                </button>
                <!-- MVP emoji picker: the 8 common reactions, Slack-style. -->
                <div v-if="reactionPickerFor === m.id" class="rx-picker">
                  <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="rx-pick" @click="onReact(m, e)">
                    {{ e }}
                  </button>
                </div>
              </div>
            </div>
          </template>

          <!-- 比时间线上每一条消息都新的「已派出」标记 —— 刚拆出去、之后房间里还
             没人说过话的那些子话题。 -->
          <DispatchedMarker
            v-for="marker in splitMarkers.tail"
            :key="marker.topicId"
            :marker="marker"
            @open="emit('open-topic', $event)"
          />

          <!-- 芝士 working indicator (Slack-style: no token streaming). Shown
             from summon until the turn's FIRST message lands; the live
             working-log checklist stays visible for the whole turn. -->
          <div v-if="awaitingReply || todoItems.length" class="im-row">
            <div class="im-gutter">
              <CheeseAvatar :size="28" />
            </div>
            <div class="im-main">
              <div class="im-meta">
                <span class="im-name">芝士</span>
              </div>

              <!-- Working-log checklist (芝士's tasks, §3.1.1). Live during a
                 turn; between turns this is the topic's stored 进度层 (#187),
                 labelled so a leftover ◐ is not read as "running right now". -->
              <div v-if="todoItems.length && todoRestored" class="todo-label">进度（上次做到这里）</div>
              <ul v-if="todoItems.length" class="todo-list">
                <li v-for="t in todoItems" :key="t.id" class="todo-item" :class="'todo-' + t.status">
                  <span class="todo-mark">{{ todoMark(t.status) }}</span>
                  <span class="todo-text">{{ t.subject }}</span>
                </li>
              </ul>

              <!-- Instant ack before the first message / during cold start -->
              <div v-if="awaitingReply" class="im-text">
                <span class="text-medium-emphasis">芝士 正在看…</span>
                <span class="caret" />
              </div>
            </div>
          </div>

          <!-- End of the conversation timeline — GitHub PR's merge box. Host fills. -->
          <div class="px-4">
            <slot name="timeline-end" />
          </div>
        </div>
      </div>

      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="chat-error-toast"
        closable
        @click:close="errorMsg = null"
      >
        {{ errorMsg }}
      </v-alert>

      <!-- B3: replying-to indicator — the next message threads under this one. -->
      <div v-if="replyTarget" class="reply-bar">
        <v-icon size="14" class="me-1">mdi-reply</v-icon>
        <span class="reply-bar__text"> 回复 {{ displayName(replyTarget) }}：{{ replySnippet(replyTarget) }} </span>
        <v-btn icon="mdi-close" size="x-small" variant="text" density="comfortable" @click="clearReply" />
      </div>

      <!-- Built-in composer (private chat / standalone use). -->
      <template v-if="showComposer">
        <v-divider />
        <div class="composer pa-2 px-3">
          <div class="d-flex align-center ga-2 mb-1">
            <!-- The ONE amber chip allowed: @芝士 toggle when ON. -->
            <button
              type="button"
              class="summon-chip"
              :class="{ 'summon-chip--on': summon }"
              title="@芝士 — 让芝士回复"
              @click="toggleSummon"
            >
              <v-icon v-if="summon" size="13">mdi-creation</v-icon>
              @芝士
            </button>
            <v-spacer />
          </div>
          <!-- @-autocomplete: pick a teammate / topic / broadcast while typing @ -->
          <div v-if="mentionMatches.length" class="mention-menu">
            <button
              v-for="(mm, i) in mentionMatches"
              :key="mm.kind + mm.insert"
              type="button"
              class="mention-menu-item"
              @click="pickMention(mm)"
            >
              <span v-if="mm.kind === 'broadcast'" class="mention-avatar mention-avatar--broadcast">
                <v-icon size="13">mdi-bullhorn-outline</v-icon>
              </span>
              <span
                v-else-if="mm.kind === 'member'"
                class="mention-avatar"
                :class="{ 'mention-avatar--agent': mm.agent }"
                >{{ mm.label.slice(0, 1).toUpperCase() }}</span
              >
              <span v-else class="mention-avatar mention-avatar--topic">
                <v-icon size="13">mdi-pound</v-icon>
              </span>
              <span class="mention-menu-name">{{ mm.label }}</span>
              <span v-if="mm.agent" class="mention-agent-badge">Agent</span>
              <span class="mention-menu-sub">{{ mm.sub }}</span>
              <span v-if="i === 0" class="mention-menu-hint">Enter</span>
            </button>
          </div>
          <!-- 图片输入: images waiting to go with the next send. -->
          <div v-if="pendingAtts.length || attsUploading" class="att-strip">
            <div v-for="(a, i) in pendingAtts" :key="a.path" class="att-thumb">
              <img :src="attachmentRawUrl(topic.id, a.path)" :alt="a.path" />
              <button type="button" class="att-remove" title="移除" @click="removePendingAtt(i)">×</button>
            </div>
            <v-progress-circular v-if="attsUploading" indeterminate size="18" width="2" />
          </div>
          <div class="d-flex align-end ga-2">
            <v-textarea
              ref="composerInput"
              v-model="draft"
              variant="plain"
              rows="1"
              auto-grow
              max-rows="6"
              hide-details
              density="comfortable"
              class="composer-input flex-grow-1"
              placeholder="发条消息…（Enter 发送，Shift+Enter 换行，可直接粘贴图片）"
              :disabled="!connected"
              @keydown="onComposerKey"
              @paste="onComposerPaste"
              @compositionstart="onCompositionStart"
              @compositionend="onCompositionEnd"
            />
            <input
              ref="fileInput"
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              class="d-none"
              @change="onFilePicked"
            />
            <v-btn
              icon="mdi-image-plus-outline"
              variant="text"
              size="small"
              title="发送图片"
              :disabled="!connected"
              @click="pickFiles"
            />
            <v-btn
              color="primary"
              variant="flat"
              icon="mdi-send"
              size="small"
              :disabled="!connected || (!draft.trim() && !pendingAtts.length)"
              @click="sendDraft"
            />
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

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
/* Action cards (§3.1.1 控件) — 芝士's cheese actions as clickable affordances.
   Same visual language as the reaction chips / event pills: quiet fill, hairline
   border, an amber platform dot marking "the platform recorded this". */
/* System lines are ONE visual family: centered, small, muted — whether they
   carry an action link (amber dot = platform act) or are plain notices. */
.action-card {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 8px 16px;
  padding: 0;
  font-size: 12px;
  color: var(--faint);
}
.chat-error-toast {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 30;
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
}
.action-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 0.8rem;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
}
.action-link:hover {
  text-decoration: underline;
}
.action-verb {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--muted, #666);
}
.action-verb::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  flex: none;
}
/* Working-log checklist (§3.1.1) — process, sits above the streaming text.
   Between turns the same list shows the stored 进度层 (#187) under a label. */
.todo-label {
  font-size: 12px;
  color: var(--muted, #666);
  margin: 2px 0 0;
}
.todo-list {
  list-style: none;
  margin: 2px 0 6px;
  padding: 6px 10px;
  border-left: 2px solid var(--v-theme-primary, #6750a4);
  background: rgba(103, 80, 164, 0.05);
  border-radius: 4px;
}
.todo-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 0.85rem;
  line-height: 1.5;
}
.todo-mark {
  width: 1em;
  flex: none;
  text-align: center;
}
.todo-pending {
  color: var(--text-muted, #888);
}
.todo-in_progress {
  color: var(--v-theme-primary, #6750a4);
  font-weight: 600;
}
.todo-completed {
  color: var(--text-muted, #999);
}
.todo-completed .todo-text {
  text-decoration: line-through;
  opacity: 0.7;
}
.messages {
  background: var(--surface);
  /* A flex child's implicit min-height is its content — without this, a long
     timeline refuses to shrink and pushes whatever follows (error alert,
     reply bar) below the pane edge, clipped. */
  min-height: 0;
}
.composer {
  background: var(--surface);
}
.composer-input :deep(textarea) {
  font-size: 14px;
  line-height: 1.5;
}

/* @-autocomplete popup — mirrors WorkspaceView's composer picker. */
.mention-menu {
  display: flex;
  flex-direction: column;
  margin-bottom: 6px;
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}
.mention-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  min-height: 36px;
  text-align: left;
  font-size: 0.85rem;
  cursor: pointer;
}
.mention-menu-item:hover {
  background: var(--fill, #f5f5f5);
}
.mention-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  font-size: 0.7rem;
  font-weight: 700;
  color: #fff;
  background: #8a94a3;
  flex: none;
}
.mention-avatar--agent {
  background: var(--accent, #f57f17);
}
.mention-avatar--broadcast {
  background: var(--ink, #33373d);
}
.mention-avatar--topic {
  background: var(--fill, #f0f1f3);
  color: var(--muted, #6b6b6b);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0 5px;
  border-radius: 4px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.mention-menu-sub {
  font-size: 0.75rem;
  color: var(--text-muted, #999);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--text-muted, #aaa);
}

/* The ONE amber chip allowed: @芝士 toggle when ON. OFF = neutral. */
.summon-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 9px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  background: var(--fill);
  color: var(--muted);
  cursor: pointer;
  transition: all 0.12s ease;
}
.summon-chip--on {
  background: var(--accent);
  color: #fff;
}

/* ---- GitHub PR header ---- */
.pr-header {
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.pr-title {
  line-height: 1.3;
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
  border-radius: 6px;
}
.pr-state--open {
  color: #fff;
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
  border-radius: 4px;
  font-weight: 500;
  color: var(--muted);
}

/* ---- Feishu group chat rows (Fix 2) ---- */
.im-row {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 16px;
  margin-top: 8px;
}
/* continuation rows of the same author sit tight under the first */
.im-row--cont {
  margin-top: 0;
}
.im-row:hover {
  background: var(--fill);
}
.im-gutter {
  width: 28px;
  flex: 0 0 28px;
}
/* Human avatar: colored initial on a per-user deterministic background
   (hash → hue), matching the Space avatars' visual language. */
.im-avatar {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--fill);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  border-radius: 8px;
}
.im-main {
  min-width: 0;
  flex: 1 1 auto;
}
.im-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}
.im-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.im-time {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--faint);
}
.im-text {
  font-size: 14px;
  line-height: 1.62;
  color: var(--text);
  word-break: break-word;
}
/* 现场尊重原文: exactly what the human typed, line breaks included. */
.im-text--verbatim {
  white-space: pre-wrap;
}
/* 图片输入: an image message — bounded thumbnail, click opens the original. */
.im-image-link {
  display: inline-block;
  margin-top: 2px;
  line-height: 0;
}
.im-image {
  max-width: min(360px, 100%);
  max-height: 260px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--fill);
  object-fit: contain;
}
/* Pending images above the composer, each with a remove button. */
.att-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 6px 2px;
}
.att-thumb {
  position: relative;
  line-height: 0;
}
.att-thumb img {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--fill);
}
.att-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.att-remove:hover {
  color: var(--ink);
}
/* Live link from an upgraded block to its new topic. */
/* B3: the "回复 X：…" cue above a reply, and the composer reply-to bar. */
.im-replied {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  margin-bottom: 3px;
  padding: 1px 6px;
  font-size: 12px;
  color: var(--ink-3, #8a8f98);
  background: var(--fill);
  border-radius: 5px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.im-replied:hover {
  color: var(--accent-ink);
}
.reply-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--ink-2, #656a72);
  background: var(--fill);
  border-top: 1px solid var(--line);
}
.reply-bar__text {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.im-upgraded {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--accent-ink);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  cursor: pointer;
}
.im-upgraded:hover {
  background: var(--surface);
  border-color: var(--accent);
}
/* @mention: neutral inset, ink text — not amber. */
.im-text :deep(.mention) {
  color: var(--v-theme-primary, #6750a4);
  background: var(--fill);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
/* @person handle reads as a link: persistent accent underline. File/topic
   refs (📄/#) keep their chip look and only underline on hover, below. */
.im-text :deep(.mention:not(.file-ref):not(.topic-ref)) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
.im-text :deep(.mention:hover) {
  text-decoration: underline;
}

/* per-row hover action bar (Feishu), floats at the row's top-right */
.im-actions {
  position: absolute;
  top: -12px;
  right: 12px;
  display: flex;
  gap: 2px;
  padding: 3px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 3px 10px rgba(25, 26, 28, 0.09);
  opacity: 0;
  transition: opacity 0.12s ease;
  pointer-events: none;
}
/* One quiet square button per action: muted ink, fill on hover — the harsh
   default round icon-buttons inside a rounded pill read as unfinished. */
.im-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--muted);
  cursor: pointer;
  transition:
    background 0.1s ease,
    color 0.1s ease;
}
.im-act:hover {
  background: var(--fill);
  color: var(--ink);
}
.im-act--on {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.im-row:hover .im-actions,
.im-actions--open {
  opacity: 1;
  pointer-events: auto;
}

/* ---- Emoji reactions (Slack) ---- */
/* MVP picker: a strip of the 8 common emoji, floating under the action bar. */
.rx-picker {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  display: flex;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(25, 26, 28, 0.12);
  z-index: 5;
}
.rx-pick {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius: 6px;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.rx-pick:hover {
  background: var(--fill);
}
/* 选项问题 buttons (cheese ask): quiet outlined pills, amber on hover. */
.ask-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.ask-option {
  border: 1px solid var(--line-2);
  background: var(--surface);
  border-radius: 8px;
  padding: 5px 14px;
  font-size: 0.85rem;
  cursor: pointer;
  transition:
    border-color 0.12s,
    background 0.12s;
}
.ask-option:hover {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}
.ask-option:disabled {
  opacity: 0.5;
  cursor: default;
}
.ask-answered {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.82rem;
  color: var(--muted);
}

/* Reaction chips under a message: emoji + count; own reactions get the amber
   outline (Slack's "you reacted" affordance). */
.rx-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.rx-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: 11px;
  background: var(--fill);
  font-size: 12px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  transition: border-color 0.12s ease;
}
.rx-chip:hover {
  border-color: var(--accent);
}
.rx-chip--mine {
  border-color: var(--accent);
  background: var(--surface);
  color: var(--ink);
}
.rx-emoji {
  font-size: 13px;
}
.rx-count {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

/* system / event line: centered, faint, small */
.platform-incident {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 12px 16px;
  padding: 13px 15px 13px 14px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, #c65a1e 28%, var(--line));
  border-radius: 10px;
  background: linear-gradient(105deg, rgb(198 90 30 / 9%), transparent 38%), var(--surface);
  box-shadow:
    inset 3px 0 0 #c65a1e,
    0 6px 20px rgb(73 35 16 / 6%);
}
.platform-incident::after {
  position: absolute;
  top: -18px;
  right: -12px;
  width: 76px;
  height: 76px;
  border: 1px solid rgb(198 90 30 / 10%);
  border-radius: 50%;
  content: '';
}
.platform-incident__icon {
  position: relative;
  z-index: 1;
  display: inline-flex;
  flex: 0 0 34px;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid rgb(198 90 30 / 22%);
  border-radius: 9px;
  color: #b64717;
  background: rgb(198 90 30 / 10%);
}
.platform-incident__content {
  position: relative;
  z-index: 1;
  min-width: 0;
}
.platform-incident__eyebrow {
  margin-bottom: 2px;
  color: #a84417;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
}
.platform-incident__title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 680;
  line-height: 1.35;
}
.platform-incident__body {
  margin-top: 4px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.55;
}
.platform-incident__status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  color: #9a4a1f;
  font-size: 11px;
  font-weight: 600;
}
.platform-incident__pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #d97706;
  box-shadow: 0 0 0 3px rgb(217 119 6 / 14%);
  animation: incident-pulse 1.8s ease-out infinite;
}
@keyframes incident-pulse {
  50% {
    box-shadow: 0 0 0 6px rgb(217 119 6 / 0%);
  }
}
@media (prefers-reduced-motion: reduce) {
  .platform-incident__pulse {
    animation: none;
  }
}

.im-event {
  text-align: center;
  color: var(--faint);
  margin: 10px 0;
  padding: 0 16px;
  font-size: 12px;
}
.im-event span {
  display: inline-block;
  padding: 0 10px;
}

/* 后端报错: collapsed by default — one quiet line among the system lines, with
   the traceback behind a click. Louder than 编辑了文档, quieter than a platform
   incident card. */
.backend-error {
  margin: 8px 16px;
  padding: 6px 10px;
  border: 1px solid color-mix(in srgb, #c65a1e 22%, var(--line));
  border-radius: 8px;
  background: color-mix(in srgb, #c65a1e 5%, transparent);
  font-size: 12px;
}
.backend-error__line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  color: var(--text-muted, var(--faint));
  list-style: none;
}
.backend-error__line > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.backend-error__count {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, #c65a1e 16%, transparent);
  font-variant-numeric: tabular-nums;
}
.backend-error__meta {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  color: var(--faint);
  font-size: 11px;
}
.backend-error__stack {
  margin: 6px 0 0;
  max-height: 320px;
  overflow: auto;
  padding: 8px;
  border-radius: 6px;
  background: var(--surface-sunken, rgb(0 0 0 / 4%));
  color: var(--faint);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre;
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
/* Rendered markdown for 芝士's replies (v-html → :deep). */
.md-content {
  font-size: 0.9rem;
  line-height: 1.6;
}
.md-content :deep(p) {
  margin: 0 0 8px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) {
  font-size: 1.02em;
  font-weight: 600;
  margin: 10px 0 4px;
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li::marker) {
  color: var(--faint);
}
.md-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
  overflow-wrap: anywhere;
}
.md-content :deep(a:hover) {
  text-decoration: underline;
}
.md-content :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
}
.md-content :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: 4px;
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 11px 13px;
  border-radius: 8px;
  overflow-x: auto;
}
.md-content :deep(pre) code {
  background: none;
  padding: 0;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
