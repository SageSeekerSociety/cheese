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

// 每话题草稿 (飞书语义): what you had typed, who you were replying to, and the
// images waiting to go — all belong to the topic they were composed in. Module
// scope so they survive this component unmounting, same as scrollMemory.
//
// 之前只有待发图片被清掉，文字和回复目标原地不动地跟着你换话题：打了一半的话
// 可能发错房间，而**回复目标**更糟——它指向的块在另一个话题里，屏幕上看不出
// 异常（本话题找不到父块就不画引用条），库里的会话树已经串了。
interface ComposerDraft {
  draft: string
  reply: Block | null
  atts: ChatAttachment[]
  /** 还没落库的消息。它们是发给**这个**话题的，跟着它走，不跟着屏幕走。 */
  outbox: Outgoing[]
}
const composerMemory = new Map<string, ComposerDraft>()

/** 发件箱里一条还没落库的消息。 */
interface Outgoing {
  clientId: string
  content: string
  summon: boolean
  replyTo?: string
  atts?: ChatAttachment[]
  /** queued = 还没送出去（没连上）; sending = 送出了在等回声; failed = 等超了 */
  state: 'queued' | 'sending' | 'failed'
}
</script>

<script setup lang="ts">
import type {
  Block,
  ChatAttachment,
  ProjectMemberRow,
  ReactionAgg,
  RoomTask,
  TodoItem,
  Topic,
  TopicMemberRow,
  WsClientChatMessage,
  WsClientMessage,
  WsServerFrame,
} from '../cx_types'

import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { useEventListener } from '@vueuse/core'

import {
  answerOptions,
  ApiError,
  attachmentRawUrl,
  chatWsUrl,
  downloadFile,
  getProgress,
  isRetryableGetFailure,
  listBlocks,
  listRoomTasks,
  listTopicMembers,
  summonAgent,
  toggleReaction as apiToggleReaction,
} from '../api'
import { usePendingAttachments } from '../lib/attachments'
import { cachedWindow, setCachedWindow } from '../lib/blockCache'
import { mergeRefreshedTail, PAGE_SIZE, prependOlder, scrollTopAfterPrepend, shouldLoadOlder } from '../lib/blockPaging'
import { parseDiffLines } from '../lib/diff'
import { expandMentions as expandMentionNames } from '../lib/expandMentions'
import { collapseNotices } from '../lib/platformNotice'
import {
  coalesceSplitFencedCodeBlocks,
  renderMarkdown as renderMarkdownWith,
  renderPlain as renderPlainWith,
} from '../lib/renderMessage'
import { placeSplitMarkers } from '../lib/splitMarkers'
import { topicShortId, topicStateBadge } from '../lib/topicState'
import { myHandle } from '../me'
import { avatarColor, avatarInitial } from '../utils/avatar'
import { getAvatarUrl } from '../utils/materials'

import LoadingSkeleton from './common/LoadingSkeleton.vue'
import AgentControls from './AgentControls.vue'
import CheeseAvatar from './CheeseAvatar.vue'
import DispatchedMarker from './DispatchedMarker.vue'
import TimelineMark from './TimelineMark.vue'

// Message rendering (markdown / plain / reference chips) lives in
// ../lib/renderMessage so it's unit-testable; here we just bind the
// handle→name and id→title maps filled from the roster / topics props.
const mentionNames = reactive<Record<string, string>>({})
const topicTitles = reactive<Record<string, string>>({})
const refMaps = { mentionNames, topicTitles }

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, refMaps)
}

function docDiffText(line: string): string {
  const text = line.slice(1)
  return /^(?:\s|&nbsp;)*$/.test(text) ? '' : text
}

function renderPlain(text: string): string {
  return renderPlainWith(text, refMaps)
}

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
  // ⤴ 升级为话题 (eval A1): the parent upgrades this message block into a topic.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
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

// 这个房间里有谁。
//
// @ 的候选不能只看项目名册：**芝士的座位在话题名册上**，一个话题一个分身
// （handle 是 cheese-<话题 hex>），项目名册上没有它——种子数据里有一行共用的
// `cheese` 掩盖过这件事，真实部署里没有。名单里少了它，就没人 @ 得到它，而 @ 它
// 正是叫它干活的唯一方式。
const roomMembers = ref<TopicMemberRow[]>([])

// 手上这份名单是**哪个房间**的。
//
// 名册按话题拉，切话题的那一瞬间上一份还在内存里。而「名单里没有 AI 队友」在两
// 种状态下含义正相反：还没到（要等——此刻替人写的 @ 指不到这个房间那位）、到了
// 确实没有（老话题没有自己的座位，得退回项目名册上那行共用的芝士）。一句「load
// 完没完」的布尔分不开这两件事，所以记的是名单的主人。
const rosterFor = ref<string | null>(null)
const rosterLoaded = computed(() => !!props.topic && rosterFor.value === props.topic.id)

async function loadRoster() {
  const place = props.topic
  if (!place) {
    roomMembers.value = []
    rosterFor.value = null
    return
  }
  const id = place.id
  try {
    const payload = await listTopicMembers(id)
    if (props.topic?.id === id) {
      roomMembers.value = payload.data
      rosterFor.value = id
    }
  } catch {
    // 名单拉不到就说出来：@ 补全会缺人（包括芝士）。静默的话，表现是「@ 不出
    // 芝士」，而屏幕上没有任何东西说明为什么。
    errorMsg.value = '成员名单加载失败，@ 补全可能不全'
  }
}

watch(() => props.topic?.id, loadRoster, { immediate: true })

// 名册那一行有三种形状：话题名册是 member_handle，项目名册是 user_handle，而 @
// 补全名单已经把它们归一到 handle 了。这里只关心「它叫什么、它的 handle 是哪个」。
type RosterRow = { name?: string; handle?: string; member_handle?: string; user_handle?: string }
function seatOf(row: RosterRow | null | undefined): { handle: string; label: string } | null {
  const handle = row?.member_handle || row?.user_handle || row?.handle
  return handle ? { handle, label: row?.name || handle } : null
}

// 这个房间名册上坐着的 AI 队友。座位是**每个话题一份**的（handle 带话题后缀），
// 项目名册上那行共用的 `cheese` 不是它。
//
// 名册没到时是 null，不拿项目那位顶：那一位也叫「芝士」，顶上去的后果是消息里那
// 个 @ 指到另一个身份，读的人以为叫了这个房间的它。
const roomAgentSeat = computed(() => (rosterLoaded.value ? seatOf(roomMembers.value.find((m) => m.agent)) : null))

// 这个房间现在交给的是哪个 AI 队友。名册那一行说了算（后端把芝士那一行的名字
// 解析成当前队友的名字）。界面上任何一处写死「芝士」，换完队友都不会变，看起来
// 就是「换人没生效」——这正是它被报上来的样子。
//
// 名册到了、这个房间确实没有 AI 座位（座位是后来才有的，老话题没有）时，退回
// 项目名册上那行共用的芝士——否则这个话题永远叫不动它。名册还没到时两边都不猜，
// 就写「芝士」：那一刻界面上任何一处说出的名字，都可能是上一个房间那位。
const agentName = computed(() => {
  const seat = roomAgentSeat.value
  if (seat) return seat.label
  if (!rosterLoaded.value) return '芝士'
  return seatOf(props.members.find((m) => m.agent))?.label || '芝士'
})

// 输入框那一行提示语。和芝士私聊时它**不能**说「交给它做」：私聊不占机器，那边
// 的芝士没有工具，读不了文件也跑不了命令。一句承诺它做不到的事的提示语，换来的
// 是一次「我试了但做不了」，而人只会记得是它没做成。
const composerHint = computed(() =>
  props.alwaysSummon ? `和${agentName.value}聊聊，或交给它一件事…` : `输入消息，@${agentName.value} 交给它做`
)

/** @ 得到的人：这个房间里的，加上项目里还没进这个房间的。 */
const mentionPool = computed(() => {
  // 名册没到（切话题的那一瞬间）房间那半就是空的：宁可少一行，也不能把**上一个
  // 房间**的座位留在名单里——那一位的名字也写着「芝士」，@ 出来却是个不在这儿的
  // handle。人在项目名册上照样 @ 得到，缺的只是这一个房间自己的那几行。
  const room = (rosterLoaded.value ? roomMembers.value : []).map((m) => ({
    handle: m.member_handle,
    label: m.name || m.member_handle,
    agent: !!m.agent,
  }))
  const inRoom = new Set(room.map((r) => r.handle))
  // 这个房间已经有自己的芝士时，项目名册上那种共用的 agent 行就不进名单了：
  // 两行都叫「芝士」的话，@芝士 展开成哪一个纯看顺序。房间里那位才是会动的那个。
  const roomHasAgent = room.some((r) => r.agent)
  const rest = props.members
    .filter((m) => !inRoom.has(m.user_handle) && !(roomHasAgent && m.agent))
    .map((m) => ({ handle: m.user_handle, label: m.name || m.user_handle, agent: !!m.agent }))
  return [...room, ...rest]
})

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

// Slack-style discrete messages: 芝士 doesn't stream tokens — each complete
// message lands as an `assistant_block` frame. `awaitingReply` drives the
// 正在看… indicator from summon until every active turn explicitly finishes.
const awaitingReply = ref(false)
const activeTurnIds = ref<Set<string>>(new Set())
watch(awaitingReply, (v) => emit('working', v))

// Tool actions 芝士 performed this turn (施工现场, spec §9.1) — ephemeral.
// Working-log todo (芝士's Task tools). Live during a turn (§3.1.1); between
// turns it holds the topic's stored 进度层 (#187) instead of being wiped, so
// "做到哪了" is visible in the room without summoning anyone.
const todoItems = ref<TodoItem[]>([])
// The list on screen is a previous turn's leftovers, not this turn's live
// progress — labelled differently so nobody reads a stale half-circle as
// "running now".
const todoRestored = ref(false)
// Action cards: 芝士's cheese actions (decision/doc/...) are persisted as system
// event blocks tagged refs=["action:<resource>"] and rendered as clickable cards.
// 只有按钮文案在这里。动作行那句话由后端写进块内容（`_ACTION_LABEL` /
// `编辑了文档`），这里曾经并排放着一份 `verb` 副本，谁都没读过它，改了也不会
// 生效——两份会漂移的文案里，看不见的那份最危险。
const ACTION_META: Record<string, { btn: string }> = {
  doc: { btn: '查看文档' },
  decision: { btn: '查看决策记录' },
  topics: { btn: '' },
  milestone: { btn: '查看日历' },
  accept: { btn: '前往验收' },
  notify: { btn: '' },
}

// 三态用图标而不是文字符号（✓ / ◐ / ○）：那三个字符的字重和基线随系统字体变，
// 在 13px 上 ◐ 和 ○ 几乎分不开。三个 mdi 图标按「填充程度」递进，一眼可分——
// 空心圈 = 还没做，半填充 = 正在做，实心圈里带勾 = 做完了。
function todoIcon(status: string): string {
  if (status === 'completed') return 'mdi-check-circle'
  if (status === 'in_progress') return 'mdi-circle-slice-4'
  return 'mdi-circle-outline'
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
    errorMsg.value = e instanceof Error ? e.message : '表情未能更新'
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
  else if (el.dataset.topic) emit('open-topic', el.dataset.topic)
  else if (el.dataset.file) {
    const row = el.closest('[data-mid]') as HTMLElement | null
    const task = rows.value.find(({ block }) => block.id === row?.dataset.mid)?.block.task_id
    emit('open-file', el.dataset.file, task ?? null)
  }
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
let disposed = false

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
  stopHeartbeat()
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

function requeueSending() {
  for (const item of outbox.value) {
    if (item.state === 'sending') {
      clearEchoTimer(item.clientId)
      item.state = 'queued'
    }
  }
}

// OPEN is only the browser's last observation: a socket whose path stopped
// carrying frames stays OPEN until TCP gives up, which took 6.5 minutes once.
// Whoever decides the link is gone (no echo for a sent message, no answer to a
// ping) comes here: drop that socket without telling it, queue what it was
// carrying, and let loadTopic reconcile history and open a fresh one.
function replaceStaleSocket() {
  const topic = props.topic
  const stale = socket
  if (!topic || !stale) return false
  requeueSending()
  stopHeartbeat()
  socket = null
  stale.onopen = null
  stale.onmessage = null
  stale.onerror = null
  stale.onclose = null
  stale.close()
  connected.value = false
  void loadTopic(topic)
  return true
}

// Liveness probe. A page that is only waiting for 芝士's reply sends nothing,
// so without this a dead link is noticed only when the next message goes
// unanswered. Any frame counts as an answer — the reply is traffic too.
const HEARTBEAT_INTERVAL_MS = 15_000
const HEARTBEAT_TIMEOUT_MS = 10_000
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let pongTimer: ReturnType<typeof setTimeout> | null = null

function noteHeartbeatAnswer() {
  if (pongTimer) clearTimeout(pongTimer)
  pongTimer = null
}

function stopHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer)
  heartbeatTimer = null
  noteHeartbeatAnswer()
}

function startHeartbeat(ws: WebSocket) {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (socket !== ws || ws.readyState !== WebSocket.OPEN || pongTimer) return
    const ping: WsClientMessage = { type: 'ping' }
    ws.send(JSON.stringify(ping))
    pongTimer = setTimeout(() => {
      pongTimer = null
      if (socket === ws) replaceStaleSocket()
    }, HEARTBEAT_TIMEOUT_MS)
  }, HEARTBEAT_INTERVAL_MS)
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
    startHeartbeat(ws)
    // State frames are transient. A doc saved while disconnected may have no
    // remaining turn to replay it; refresh through the panel's conflict guard.
    emit('state-changed', 'doc')
    flushOutbox() // 断线期间打的字，连上就自己走
  }
  ws.onclose = () => {
    if (socket === ws) {
      connected.value = false
      stopHeartbeat()
      // Anything still waiting for an echo lost its channel — queue it again
      // rather than let its timer call it undelivered while we reconnect.
      requeueSending()
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
    noteHeartbeatAnswer()
    if (frame.type === 'pong') return
    handleFrame(frame)
    noteCatchUpFrame()
  }
}

// 有网就自动转出来 (owner spec): the offline→online transition is our cue to
// reconnect NOW rather than wait out the backoff, and to refetch history so
// messages that landed during the outage are pulled in — loadTopic re-runs the
// history fetch and reopens the socket, and pushBlock dedups the overlap. Guards:
// a connect refusal is an auth problem, not an outage (leave it latched); a
// still-healthy socket needs nothing; no active topic, nothing to do.
function reconnectOnOnline() {
  if (connectRefused.value || connected.value || !props.topic) return
  cancelRetry()
  retryDelayMs = 1000 // recovered → next outage starts backoff fresh
  void loadTopic(props.topic)
}
useEventListener(window, 'online', reconnectOnOnline)

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
      settleOutbox(frame.block)
      pushBlock(frame.block)
      autoScroll()
      break
    case 'reaction':
      // Someone toggled an emoji / 芝士's ✅ receipt landed — update the chip
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
      pushBlock(frame.block)
      // Compatibility with an older backend that has no lifecycle markers.
      if (activeTurnIds.value.size === 0) awaitingReply.value = false
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
      messages.value = messages.value.filter((m) => m.id !== frame.block_id)
      break
    case 'turn_active':
      if (frame.turn_ids?.length) activeTurnIds.value = new Set(frame.turn_ids)
      awaitingReply.value = true
      break
    case 'turn_started': {
      const next = new Set(activeTurnIds.value)
      next.add(frame.turn_id)
      activeTurnIds.value = next
      awaitingReply.value = true
      break
    }
    case 'turn_finished': {
      const next = new Set(activeTurnIds.value)
      next.delete(frame.turn_id)
      activeTurnIds.value = next
      awaitingReply.value = next.size > 0
      todoRestored.value = true
      emit('turn-done')
      autoScroll()
      break
    }
  }
}

async function loadTopic(topic: Topic) {
  errorMsg.value = null
  connectRefused.value = false // a fresh topic gets a fresh attempt at connecting
  awaitingReply.value = false
  activeTurnIds.value = new Set()
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
  unreadAnchorId.value = null
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
    if (disposed || props.topic?.id !== topic.id) return
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
    // A reconnect starts with durable history. Settle sends that landed while
    // their echo was lost before opening the new socket; only absent client ids
    // remain queued for an idempotent resend.
    for (const block of merged.blocks) settleOutbox(block)
    hasMore.value = merged.hasMore
    setCachedWindow(topic.id, merged)
    placeUnreadAnchor() // 冻在这一刻：之后来的新消息不再移动这条线
    if (!cached) restoreScroll(topic.id)
    else if (grew && atBottom.value) autoScroll()
    openSocket(topic.id)
    void fillViewportIfNeeded()
  } catch (e) {
    if (disposed || props.topic?.id !== topic.id) return
    errorMsg.value = e instanceof Error ? e.message : '加载历史失败'
    // A failed history fetch must not terminate socket recovery during an outage.
    if (isRetryableGetFailure('GET', e instanceof ApiError ? e.status : undefined, e)) {
      scheduleReconnect(topic.id)
    }
  } finally {
    if (props.topic?.id === topic.id) loadingHistory.value = false
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
function parentOf(m: Block): Block | undefined {
  return m.reply_to ? messages.value.find((x) => x.id === m.reply_to) : undefined
}
function showReplyCue(m: Block): boolean {
  // Only human replies are explicit threads. An AI message's reply_to is the
  // implicit link to the user message that triggered it — not a thread cue.
  return m.author_type === 'human' && !!parentOf(m)
}
function replySnippet(m: Block): string {
  if (m.kind === 'attachment') return isImageBlock(m) ? '[图片]' : '[文件]'
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
async function downloadAttachment(m: Block) {
  try {
    await downloadFile(imageUrl(m), m.content.split('/').pop() || 'file')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '下载失败'
  }
}
function scrollToMessage(id: string) {
  document.querySelector(`[data-mid="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

// ---- 发件箱 (§14.1 实时) ----
// 「人发的消息立即显示，绝不排在 AI turn 后面。别的都可以错，现场不能错」——
// 而在这之前，发送是把帧塞进 socket 就完了：屏幕上什么都没有，要等后端落库
// （名册查询、mention 解析、通知写入）再广播回来才显示。快的时候看不出，慢的
// 时候你会以为自己的消息发丢了；socket 没开时更直接：输入框本身是禁用的。
//
// 现在消息立刻出现在时间线末尾，再去对账：后端把 client_id 原样戳回块上，回声
// 一到就把本地这条换成真的。对不上账的那条不会消失，它变成一条能重试的行。
const outbox = ref<Outgoing[]>([])
// 等回声等多久算没送到。宁可长一点：误报「未送达」比晚一点显示更伤——房间里
// 已经有过一次这种误报（#539）。
const ECHO_TIMEOUT_MS = 30_000
const echoTimers = new Map<string, ReturnType<typeof setTimeout>>()

function clearEchoTimer(clientId: string) {
  const t = echoTimers.get(clientId)
  if (t) clearTimeout(t)
  echoTimers.delete(clientId)
}

function markFailed(clientId: string) {
  clearEchoTimer(clientId)
  const item = outbox.value.find((o) => o.clientId === clientId)
  if (!item || item.state !== 'sending') return
  // No durable echo for the full timeout: the link is gone whatever OPEN says.
  if (!replaceStaleSocket()) item.state = 'failed'
}

/** Hand one queued message to the socket, if there is one to hand it to. */
function flushOutbox() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return
  for (const item of outbox.value) {
    if (item.state === 'sending') continue
    const msg: WsClientChatMessage = {
      type: 'message',
      content: item.content,
      summon: item.summon,
      reply_to: item.replyTo,
      attachments: item.atts,
      client_id: item.clientId,
    }
    socket.send(JSON.stringify(msg))
    item.state = 'sending'
    clearEchoTimer(item.clientId)
    echoTimers.set(
      item.clientId,
      setTimeout(() => markFailed(item.clientId), ECHO_TIMEOUT_MS)
    )
  }
}

/** The echo came home — this local copy has a real block now. */
function settleOutbox(block: Block): boolean {
  const clientId = (block.meta as Record<string, unknown> | null)?.client_id
  if (typeof clientId !== 'string') return false
  const i = outbox.value.findIndex((o) => o.clientId === clientId)
  if (i < 0) return false
  clearEchoTimer(clientId)
  outbox.value.splice(i, 1)
  return true
}

function retrySend(clientId: string) {
  const item = outbox.value.find((o) => o.clientId === clientId)
  if (!item) return
  item.state = 'queued'
  flushOutbox()
}

function dropSend(clientId: string) {
  clearEchoTimer(clientId)
  outbox.value = outbox.value.filter((o) => o.clientId !== clientId)
}

function send(content: string, summon: boolean, attachments?: ChatAttachment[]): boolean {
  const trimmed = content.trim()
  const atts = attachments?.length ? attachments : undefined
  // An image-only send (no text) is a valid message (图片输入).
  if (!trimmed && !atts) return false
  errorMsg.value = null
  outbox.value.push({
    clientId: `c${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    content: trimmed,
    summon,
    replyTo: replyTarget.value?.id ?? undefined,
    atts,
    state: 'queued',
  })
  replyTarget.value = null
  flushOutbox()
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
// Historical SDK turns can contain one fenced Markdown block split across
// consecutive message rows. Repair those rows before collapseNotices hides
// event blocks, because an event is a hard boundary and must prevent an
// accidental merge.
const rows = computed(() => collapseNotices(coalesceSplitFencedCodeBlocks(messages.value)))
const visible = computed<Block[]>(() => rows.value.map((r) => r.block))

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

const splitMarkers = computed(() =>
  placeSplitMarkers(roomTasks.value, {
    blocks: visible.value,
    hasMore: hasMore.value,
  })
)

// ---- Feishu group-chat helpers (Fix 2) ----
// handle → 名册行。**不要**改用 mentionNames：那张表额外塞了 all/here 两个保留
// 键（渲染成「所有人」「在线成员」），一个恰好叫 all 的用户会被显示成「所有人」。
const memberByHandle = computed(() => {
  const map = new Map<string, ProjectMemberRow>()
  for (const row of props.members) map.set(row.user_handle, row)
  return map
})
// 消息里存的 author 是登录身份的 handle（后端有意固定成这个，防伪造），所以
// 「显示成昵称」只能在这里做：查名册，查不到（退出项目的人、anonymous 兜底
// 作者）就把 handle 原样显示出来。
function displayName(m: Block): string {
  if (m.author_type === 'ai') return agentName.value
  return memberByHandle.value.get(m.author)?.name || m.author
}
// 真头像加载失败过的 handle —— 退回彩色首字母，不留破图。
const avatarBroken = ref<Set<string>>(new Set())
function avatarSrc(handle: string): string | null {
  if (avatarBroken.value.has(handle)) return null
  const id = memberByHandle.value.get(handle)?.avatar_id
  // 名册上没这个人、或这行没有头像时返回 null：宁可留一个按 handle 哈希、认得出
  // 是谁的色块，也不要 getAvatarUrl(undefined) 给陌生人配一张 /avatars/default。
  return id == null ? null : getAvatarUrl(id)
}
function onAvatarError(handle: string): void {
  if (avatarBroken.value.has(handle)) return
  avatarBroken.value = new Set(avatarBroken.value).add(handle)
}
// 自己在名册上的名字（发件箱那几行用它，因为它们还没有作者字段）。
const myName = computed(() => memberByHandle.value.get(AUTHOR)?.name || AUTHOR)

function outgoingState(item: Outgoing): string {
  if (item.state === 'failed') return '未送达'
  return connected.value ? '发送中…' : '等待连接'
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
// 按 handle 判，不按 author_type：author_type 只说「是人还是 AI」，而这一列里
// 有好几个人。
function isMine(m: Block): boolean {
  return m.author_type === 'human' && m.author === AUTHOR
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

// ---- Topic header state. The labels live in lib/topicState.ts because the
// 工作台's own topic header renders the same badge — one table, so the two can
// never disagree about what `archived` is called.
const prShortId = computed(() => topicShortId(props.topic?.id))
const prState = computed(() => topicStateBadge(props.topic?.status))

// ---- Self-contained composer (only when showComposer) ----
const draft = ref('')
// 拖文件到输入栏 (spec §7.1)。只是把落区标出来，判断留给 usePendingAttachments。
const dragOver = ref(false)
function onDropFiles(e: DragEvent) {
  dragOver.value = false
  onComposerDrop(e)
}
const composerInput = ref<{ focus?: () => void } | null>(null)

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
  visible.value.some((b) => b.author_type === 'ai' && (b.kind === 'message' || b.kind === 'attachment'))
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
  // 起手草稿里那个 @ 和按钮写进去的是同一个名字（见 `agentMention`）：写错了的话，
  // 人点完「起草文档」发出去，屋里会动的那位不动，而草稿上明明 @ 着「芝士」。
  const agent = agentMention.value
  draft.value = `${props.alwaysSummon ? '' : `@${agent?.label ?? '芝士'} `}${text}`
  void nextTick(() => composerInput.value?.focus?.())
}

// @-autocomplete (§3.1.1 人也能 @): the @token being typed at the end of the
// draft, and the teammates / topics / broadcast tokens it can complete to.
// Mirrors TopicView's composer so the root-topic and 私聊 composers get the
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
  const named: MentionItem[] = [
    ...mentionPool.value.map((m) => ({
      label: m.label,
      kind: 'member' as const,
      insert: m.label,
      sub: `@${m.handle}`,
      agent: m.agent,
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
  // Agent 排在最前，群播让位。第一格就是 Enter 的默认答案，而「打一个 @ 然后回
  // 车」在这个产品里压倒性地是「交给芝士」——把 @all 摆在那个位置，等于让最常见
  // 的一次输入默认去打扰整个话题的所有人。群播是 fixed-literal token，换个位置
  // 它还是那两个 token。
  const agents = named.filter((i) => i.agent)
  const rest = named.filter((i) => !i.agent)
  return [...agents, ...broadcast, ...rest].slice(0, 7)
})
function pickMention(item: MentionItem) {
  draft.value = draft.value.replace(/@([^\s@]*)$/, `@${item.insert} `)
  // 挑完一个人，正是你要接着往下打字的时刻。鼠标点菜单会把焦点带到那颗按钮上，
  // 键盘挑则让整块菜单从 DOM 里消失——两条路都可能把光标从输入框里带走，而「@
  // 完人还要再点一次输入框」是这个面板最烦人的地方。
  void nextTick(() => composerInput.value?.focus?.())
}

// Human composer: turn a friendly "@名字 / @话题名 / @handle" into the canonical
// token (<@handle> / <#topicId>) at send time. The rules live in the shared
// module so this stays identical to the backend's backstop.
function expandMentions(text: string): string {
  return expandMentionNames(
    text,
    mentionPool.value,
    props.topicList.filter((t) => t.kind !== 'root')
  )
}

// 图片输入: paste (screenshot) or pick images; they upload to the topic's
// worktree immediately and wait in a preview strip until send.
const fileInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)
const { mdAndUp } = useDisplay()
const {
  pending: pendingAtts,
  uploading: attsUploading,
  addFiles,
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
function pickFiles() {
  fileInput.value?.click()
}
// 手机上单开一个「照片」：系统的文件选择器里翻相册要好几步，而 accept=image/*
// 直接进相册/相机。桌面上不给这一颗——那儿贴一张截图或者拖进来就完事了。
function pickImages() {
  imageInput.value?.click()
}
function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void addFiles(Array.from(input.files))
  input.value = '' // allow re-picking the same file
}

// 房间里那位芝士 —— **房间名册**上坐着的那一行，不是项目名册上那行共用的。
//
// 名册没到时候没有它（按钮关着，见下面的 `summonReady`）：那时候名单里唯一带 AI
// 标记的是项目那位，认了它，正文里写下的 @ 就指到另一个身份。名册到了、这个房间
// 确实没有座位（老话题），才退回项目那一行。
const agentMention = computed(
  () => roomAgentSeat.value ?? (rosterLoaded.value ? seatOf(mentionPool.value.find((m) => m.agent)) : null)
)

// 叫不叫芝士，由**这条消息 @ 没 @ 它**决定 —— 和 @ 一个人走的是同一条路，
// 区别只在于 @ 人是通知、@ 它是真的开一轮。这以前是输入区上一个单独的开关：
// 芝士本来就在 @ 补全的名单里（`agent` 标记），于是同一个意图有两条并列的说法，
// 而只有开关那条是通的 —— 在正文里 @ 了它，它读得到，却不会动。
//
// 群播 (@all/@here) 不算：那是通知房间里的人，不是把活派给它。
//
// 认的是上面那一位的 handle，不是「名单里哪个带 AI 标记的 handle」：后者在名册
// 没到时认的是项目那位，于是正文里那个 @ 指不到房间里会动的人，而按钮和消息都写
// 着「叫了它」——两份说法，正是这个功能一开始要消灭的东西。
function mentionsAgent(expanded: string): boolean {
  const agent = agentMention.value
  return agent !== null && expanded.includes(`<@${agent.handle}>`)
}

// 这条草稿现在叫不叫它。**读的是正文**，不是一个单独存着的开关值：真相只有一条，
// 入口可以有三个（手打 @、点按钮、⌘/Ctrl+Enter 都是往正文里写同一个 @）。
// 独立开关是另一回事 —— 那种东西能和正文说不一样的话（开关亮着、正文里没有 @），
// 那时候「这条到底算不算叫了它」谁也答不上来，而只有开关那条是通的。
const summonOn = computed(() => props.alwaysSummon || mentionsAgent(expandMentions(draft.value)))
// 这个房间的芝士是谁，现在知道了吗。
const summonReady = computed(() => agentMention.value !== null)

// 名册还没到的时候不能替人写这个 @：召唤与否是浏览器按**能不能把名字解析成
// handle** 算出来的，此刻解析不出来，写进去的 @ 只是一行字，消息照发、它照样不
// 动。所以这两个入口在那一瞬间是关着的（见 `summonReady`），宁可少一个入口，
// 也不要一个点了不算数的入口。
function withAgentMention(text: string): string {
  const agent = agentMention.value
  if (!agent || mentionsAgent(expandMentions(text))) return text
  return `@${agent.label} ${text}`
}

// 「交给芝士」这颗按钮：它不改任何隐藏状态，它只是替你打那五个字，写完你看得见、
// 也能自己删掉。
function toggleSummon() {
  const agent = agentMention.value
  if (!summonOn.value) {
    draft.value = withAgentMention(draft.value)
  } else if (agent) {
    // 只摘掉第一处。正文里别处还提着它（「照 @芝士 说的改」）是在说事，不是在
    // 叫它，取消这一次召唤不该顺手把那句话也改了。
    for (const pat of [`@${agent.label}`, `@${agent.handle}`]) {
      const at = draft.value.indexOf(pat)
      if (at < 0) continue
      const after = at + pat.length
      draft.value = draft.value.slice(0, at) + draft.value.slice(draft.value[after] === ' ' ? after + 1 : after)
      break
    }
  }
  void nextTick(() => composerInput.value?.focus?.())
}

// ---- 忘了 @ 的补救 ----
// 房间里最后一句话是对着人说的，芝士就不会动 —— 这是它该有的样子（没 @ 不等于
// 没说，那条消息在待读窗口里等着下一轮捎上）。真正伤人的是**房间里没有任何东西
// 说明这一点**：一个人贴完需求等了八分钟，追问「你有看到我的问题嘛」，全程没人
// 接、也没有一行字告诉他为什么。这一行就是那行字，外加一次点击。
//
// 所以文案不能写「它还没看到」：那条消息不会丢，只是不会**现在**动。
const summonBusy = ref(false)
// 已经为哪条消息按过这一下。按完就把提示收起来，包括后端回「本来就不必」的那两
// 种情况 —— 点了一下什么都没变，看起来和坏掉一模一样。
const summonedFor = ref<string | null>(null)
function showSummonHint(m: Block, i: number): boolean {
  if (summonedFor.value === m.id) return false
  if (props.alwaysSummon || !props.showComposer) return false
  if (props.topic?.status === 'archived') return false
  // 已经在跑的那一轮会自己把没 @ 的消息接过去（后端 submit_message 的 merge
  // 分支），这时候提示「没人接」是假的。
  if (awaitingReply.value || outbox.value.length) return false
  if (i !== rows.value.length - 1) return false
  if (m.author_type !== 'human') return false
  if (m.kind !== 'message' && m.kind !== 'attachment') return false
  // 一次发送可能落成好几块（一句话 + 几张图），而叫没叫它写在那句话里。只看最后
  // 一块的话，配了图的那次发送永远会被判成「没叫」——图片块的正文是一个文件路径，
  // 它 @ 不到任何人。所以看的是同一个人连在一起的这一串。
  for (let k = rows.value.length - 1; k >= 0; k -= 1) {
    const b = rows.value[k].block
    if (b.author_type !== 'human' || b.author !== m.author) break
    if (mentionsAgent(b.content)) return false
  }
  return true
}
async function summonNow() {
  const id = props.topic?.id
  if (!id || summonBusy.value) return
  summonBusy.value = true
  try {
    const res = await summonAgent(id)
    // started=false 说明这一下本来就不必花钱（房间已经在干活，或者别人先 @ 过
    // 了）。两种都不是错，但两种都得让界面动一下。
    summonedFor.value = rows.value.at(-1)?.block.id ?? null
    if (res.started) awaitingReply.value = true
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '没能叫醒它，请重试'
  } finally {
    summonBusy.value = false
  }
}

// `summon: true` = ⌘/Ctrl+Enter「发送并交给它」。它把 @ 写进正文再发，而不是在帧
// 上把 summon 悄悄置真：时间线上那条消息必须自己说明它叫了谁，否则读的人看到的
// 是一条谁也没 @ 的消息，芝士却动了。
function sendDraft(opts?: { summon?: boolean }) {
  if (attsUploading.value) return
  if (!draft.value.trim() && !pendingAtts.value.length) return
  const content = expandMentions(opts?.summon ? withAgentMention(draft.value) : draft.value)
  if (send(content, props.alwaysSummon || mentionsAgent(content), pendingAtts.value.slice())) {
    draft.value = ''
    clearPendingAtts()
  }
}

// 每话题草稿: stash / restore everything the composer holds. Nothing here is
// "just a preference" — each field names something in the topic being left
// (a block to reply to, files already uploaded to that topic's worktree).
function rememberComposer(topicId: string) {
  const hasContent =
    !!draft.value.trim() || pendingAtts.value.length > 0 || !!replyTarget.value || outbox.value.length > 0
  if (!hasContent) composerMemory.delete(topicId)
  else
    composerMemory.set(topicId, {
      draft: draft.value,
      reply: replyTarget.value,
      atts: pendingAtts.value.slice(),
      outbox: outbox.value.slice(),
    })
}

function restoreComposer(topicId: string | undefined) {
  const saved = topicId ? composerMemory.get(topicId) : undefined
  draft.value = saved?.draft ?? ''
  replyTarget.value = saved?.reply ?? null
  pendingAtts.value = saved?.atts ?? []
  // 换话题时在飞的那些没法再等回声了（socket 换了），回到队列，等这个话题
  // 下次连上再走。它们不会在别的房间里露面。
  outbox.value = (saved?.outbox ?? []).map((o) => (o.state === 'sending' ? { ...o, state: 'queued' } : o))
}

// IME (输入法) guard — see TopicView.vue for the full story: Safari fires
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

// 触摸屏上回车是换行。软键盘没有 Shift 这一层，所以「Enter 发送 / Shift+Enter
// 换行」在手机上等于「打不出第二行」——发送有按钮，换行没有别的办法。
// 按输入方式判断，不按视口宽度：带触摸屏的笔记本两样都对。
const coarse = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
const enterSends = ref(!coarse?.matches)
coarse?.addEventListener?.('change', (e: MediaQueryListEvent) => (enterSends.value = !e.matches))

function onComposerKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey) return
  // IME composition (拼音选字/上屏) 的回车是按给输入法的，绝不当成发送。
  if (isImeKey(e)) return
  // Only act on Enter from the focused composer textarea itself.
  const t = e.target as HTMLElement | null
  if (!t || t.tagName !== 'TEXTAREA' || document.activeElement !== t) return
  // ⌘/Ctrl+Enter = 发送并交给芝士，正文里一个 @ 都不用打。判断排在 @-菜单前面：
  // 打到一半的 @ 不该把这个已经说清楚的「交给它」变成一次选人。
  if ((e.metaKey || e.ctrlKey) && summonReady.value) {
    e.preventDefault()
    sendDraft({ summon: true })
    return
  }
  // While the @-menu is open, Enter picks the first match instead of sending.
  if (mentionMatches.value.length) {
    e.preventDefault()
    pickMention(mentionMatches.value[0])
    return
  }
  if (!enterSends.value) return
  e.preventDefault()
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
    if (oldId) {
      rememberComposer(oldId)
      for (const id of [...echoTimers.keys()]) clearEchoTimer(id)
    }
    if (props.topic) {
      // loadTopic clears the pending attachments synchronously before its first
      // await, so this topic's own draft has to be restored AFTER the call.
      loadTopic(props.topic)
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
  rememberScroll() // persist position across an unmount (e.g. leaving the view)
  if (props.topic) rememberComposer(props.topic.id)
  for (const id of [...echoTimers.keys()]) clearEchoTimer(id)
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
        <div>选择一个话题开始对话</div>
      </div>
    </div>

    <template v-else>
      <!-- GitHub-PR-style header (Fix 4): only for real work topics (话题 = PR).
           The root topic (本体) and private chat use the plain header below. -->
      <div v-if="!hideHeader && prHeader" class="pr-header px-4 py-3">
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
      <div v-else-if="!hideHeader" class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2">
          <v-btn
            v-if="backLabel"
            variant="text"
            size="small"
            density="comfortable"
            prepend-icon="mdi-arrow-left"
            class="c-muted"
            @click="emit('back')"
          >
            {{ backLabel }}
          </v-btn>
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
          <LoadingSkeleton v-if="loadingHistory" variant="chat" />

          <section v-if="showStarters" class="chat-start px-5 py-8" aria-label="开始项目协作">
            <h2 class="t-title mb-2">从一件具体的事开始</h2>
            <p class="t-body c-muted mb-4">说说你想解决什么问题，@芝士 可以查资料、写文档，也能和你一起拆任务</p>
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
            <p class="t-meta mt-3">点选后补充你的需求，再发送</p>
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

          <template v-for="({ block: m, notice }, i) in rows" :key="m.id">
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
              @open="emit('open-topic', $event)"
            />
            <!-- 平台行：一种形态。lib/platformNotice.ts 决定分档，这里只按
               「严重度改记号、不改形态」画。左边缘和消息正文同一条 54px 轴 ——
               整列只有一条扫视线，右侧固定放动作/归属，「谁在等我」一眼扫得出。 -->
            <div
              v-if="notice?.mode === 'incident'"
              class="sys-row sys-row--danger platform-incident"
              role="alert"
              :data-error-code="notice.incident.code"
              data-testid="platform-error-card"
            >
              <div class="sys-line">
                <v-icon class="sys-mark" :icon="notice.incident.icon" size="15" />
                <span class="sys-text">{{ notice.incident.title }}</span>
                <span class="sys-who">{{ notice.incident.status }}</span>
              </div>
              <div class="sys-sub">{{ notice.lead }}</div>
              <details v-if="notice.rest" class="sys-more">
                <summary>{{ notice.detailLabel || '展开详情' }}</summary>
                <pre class="sys-detail">{{ notice.rest }}</pre>
              </details>
            </div>
            <!-- 本轮摘要 (spec §8.5 变更提醒): 这一轮改了什么 + 顺带更新了什么。
               「查看改动」是这一行唯一的动作 —— 采纳是话题级的一次性动作，不是
               每轮都问一遍的东西（§14.6）。 -->
            <div v-else-if="notice?.mode === 'turn-summary'" class="sys-row turn-summary">
              <div class="sys-line">
                <span class="sys-mark sys-mark--dot" aria-hidden="true" />
                <span class="sys-text">
                  <template v-if="notice.changes">
                    本轮改了 {{ notice.changes.filesTotal }} 个文件 (+{{ notice.changes.added }} −{{
                      notice.changes.removed
                    }})
                  </template>
                  <template v-for="(act, ai) in notice.actions" :key="ai">
                    <span v-if="ai > 0 || notice.changes" class="sys-sep"> · </span>
                    <span v-html="renderPlain(act.text)" />
                  </template>
                </span>
                <button
                  v-if="notice.changes"
                  type="button"
                  class="sys-action"
                  @click="emit('open-resource', 'changes', notice.turnId ?? undefined)"
                >
                  查看改动
                </button>
              </div>
              <div v-if="notice.changes?.files.length" class="sys-sub sys-files">
                {{ notice.changes.files.join(' · ')
                }}<template v-if="notice.changes.filesOmitted"> · 另 {{ notice.changes.filesOmitted }} 个</template>
              </div>
            </div>
            <!-- 芝士这轮改了平台上的什么东西（没能折进本轮摘要的那一条） -->
            <div v-else-if="notice?.mode === 'action'" class="sys-row action-card">
              <div class="sys-line">
                <span class="sys-mark sys-mark--dot" aria-hidden="true" />
                <!-- notice.text may carry a <@handle> actor token (编辑了文档): render
                   through the shared token→chip path so the actor is clickable. -->
                <span class="sys-text" v-html="renderPlain(notice.text)" />
                <button
                  v-if="ACTION_META[notice.resource]?.btn"
                  type="button"
                  class="sys-action"
                  @click="emit('open-resource', notice.resource, m.turn_id ?? undefined)"
                >
                  {{ ACTION_META[notice.resource].btn }}
                </button>
              </div>
              <details v-if="notice.detail" class="sys-more">
                <summary>{{ notice.detailLabel || '展开详情' }}</summary>
                <div v-if="notice.resource === 'doc'" class="doc-edit-diff" aria-label="文档修改对比">
                  <template v-for="(line, index) in parseDiffLines(notice.detail)" :key="index">
                    <div
                      v-if="(line.kind === 'add' || line.kind === 'del') && docDiffText(line.text)"
                      class="doc-edit-line"
                      :class="`doc-edit-line--${line.kind}`"
                      :aria-label="line.kind === 'add' ? '新增' : line.kind === 'del' ? '删除' : undefined"
                    >
                      <span class="doc-edit-mark" aria-hidden="true">{{
                        line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '
                      }}</span>
                      <span>{{ docDiffText(line.text) }}</span>
                    </div>
                  </template>
                </div>
                <pre v-else class="sys-detail">{{ notice.detail }}</pre>
              </details>
            </div>
            <!-- 后端报错 (backend_log.py): 芝士 needs the whole traceback, a
               person needs to know it happened. So the line shows by default
               and the stack is one click away — a room is a conversation, not
               a monitoring dashboard. -->
            <details
              v-else-if="notice?.mode === 'backend-error'"
              class="sys-row sys-row--warn backend-error"
              data-testid="backend-error-event"
            >
              <summary class="sys-line">
                <span class="sys-mark sys-mark--dot" aria-hidden="true" />
                <span class="sys-text">{{ notice.error.line }}</span>
                <span v-if="notice.error.count" class="sys-count">×{{ notice.error.count }}</span>
              </summary>
              <div class="sys-fold">
                <div v-if="notice.error.where || notice.error.requestId" class="sys-meta">
                  <span v-if="notice.error.where">{{ notice.error.where }}</span>
                  <span v-if="notice.error.requestId"> req {{ notice.error.requestId }} </span>
                </div>
                <pre v-if="notice.error.stack" class="sys-detail">{{ notice.error.stack }}</pre>
              </div>
            </details>
            <!-- 折叠行: CI 没过 / 闸门红了 / 轮次失败… summary 一行就够决定「出了
               什么事、归谁管」，日志和原话在一次点击之后。连着来的同类事件折成一
               条带 ×N，但每一次的原话都还在展开区里，一条都没扔。 -->
            <details
              v-else-if="notice?.mode === 'fold'"
              class="sys-row"
              :class="{ 'sys-row--warn': notice.who === 'human' }"
              data-testid="platform-notice"
            >
              <summary class="sys-line">
                <span class="sys-mark sys-mark--dot" aria-hidden="true" />
                <span class="sys-text">{{ notice.line }}</span>
                <span v-if="notice.count > 1" class="sys-count">×{{ notice.count }}</span>
                <span v-if="notice.whoLabel" class="sys-who">{{ notice.whoLabel }}</span>
              </summary>
              <div class="sys-fold">
                <div v-for="(occ, oi) in notice.occurrences" :key="oi" class="sys-occurrence">
                  <div class="sys-meta">
                    {{ occ.label || '详情' }}<template v-if="notice.count > 1"> · {{ occ.line }}</template>
                  </div>
                  <pre class="sys-detail">{{ occ.detail }}</pre>
                </div>
              </div>
            </details>
            <!-- system / event blocks. Content may carry a <@handle> actor token
               (归档/编辑…): render it through the SAME token→chip path as
               messages so the actor is a clickable mention, not raw text. -->
            <div v-else-if="notice?.mode === 'plain'" class="sys-row im-event">
              <div class="sys-line">
                <span class="sys-mark sys-mark--dot" aria-hidden="true" />
                <span class="sys-text" v-html="renderPlain(m.content)" />
              </div>
            </div>

            <!-- message row -->
            <div
              v-else-if="!notice"
              class="im-row"
              :class="{ 'im-row--cont': !isRunStart(i), 'im-row--self': isMine(m) }"
              :data-mid="m.id"
            >
              <!-- avatar gutter: only on the first of a run -->
              <div class="im-gutter">
                <template v-if="isRunStart(i)">
                  <CheeseAvatar v-if="m.author_type === 'ai'" :size="28" :name="displayName(m)" />
                  <!-- 真头像；取不到或加载失败退回按 handle 哈希的彩色首字母。
                     底色的种子继续用 handle（换成昵称会让每个人的颜色都变）,
                     变的只有色块里的字。 -->
                  <img
                    v-else-if="avatarSrc(m.author)"
                    class="im-avatar im-avatar--photo"
                    :src="avatarSrc(m.author)!"
                    :alt="displayName(m)"
                    @error="onAvatarError(m.author)"
                  />
                  <div v-else class="im-avatar" :style="{ backgroundColor: avatarColor(m.author) }">
                    {{ avatarInitial(displayName(m)) }}
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
                <v-btn
                  v-else-if="m.kind === 'attachment'"
                  variant="text"
                  prepend-icon="mdi-file-document-outline"
                  append-icon="mdi-download-outline"
                  class="text-none im-file-link"
                  :title="`下载 ${m.content.split('/').pop()}`"
                  @click="downloadAttachment(m)"
                >
                  <span class="text-truncate">{{ m.content.split('/').pop() }}</span>
                </v-btn>
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
                <!-- 活引用 (eval A1): 升级出去的块指向它变成的那个地点。房间里
                   升级出来的是一条支线，私聊里升级出来的才是房间——两个字段各指
                   一张表，同时只会有一个非空。 -->
                <button
                  v-if="m.upgraded_to_task_id || m.upgraded_to_topic_id"
                  type="button"
                  class="im-upgraded"
                  @click="emit('open-topic', (m.upgraded_to_task_id || m.upgraded_to_topic_id)!)"
                >
                  <v-icon size="13">mdi-arrow-top-right</v-icon>
                  已升级为话题，点击查看
                </button>
                <!-- 忘了 @ 的补救：房间里最后一句是对着人说的，芝士就不会动，
                   而在这一行出现之前，房间里没有任何东西说明这一点。 -->
                <div v-if="showSummonHint(m, i)" class="summon-hint">
                  <span class="summon-hint-text">这条没叫{{ agentName }}，它不会现在动</span>
                  <button type="button" class="summon-hint-btn" :disabled="summonBusy" @click="summonNow">
                    让它现在就看
                  </button>
                </div>
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
                  title="添加表情"
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

          <!-- 比时间线上每一条消息都新的「已派出」标记 —— 刚派出去、之后房间里还
             没人说过话的那些支线。 -->
          <DispatchedMarker
            v-for="marker in splitMarkers.tail"
            :key="marker.taskId"
            :marker="marker"
            @open="emit('open-topic', $event)"
          />

          <!-- 发件箱: 已经打出去、还没落库的消息。它长得就是一条自己发的消息,
             只是右边多一行状态——「立即显示」是第一位的，送达状态是第二位的。 -->
          <div v-for="item in outbox" :key="item.clientId" class="im-row im-row--pending im-row--self">
            <div class="im-gutter">
              <img
                v-if="avatarSrc(AUTHOR)"
                class="im-avatar im-avatar--photo"
                :src="avatarSrc(AUTHOR)!"
                alt=""
                @error="onAvatarError(AUTHOR)"
              />
              <div v-else class="im-avatar" :style="{ backgroundColor: avatarColor(AUTHOR) }">
                {{ avatarInitial(myName) }}
              </div>
            </div>
            <div class="im-main">
              <div class="im-meta">
                <span class="im-name">{{ myName }}</span>
                <span class="im-time">{{ outgoingState(item) }}</span>
              </div>
              <div class="im-text im-text--verbatim" v-html="renderPlain(item.content)" />
              <div v-if="item.state === 'failed'" class="outbox-actions">
                <button type="button" class="outbox-act" @click="retrySend(item.clientId)">重试</button>
                <button type="button" class="outbox-act" @click="dropSend(item.clientId)">删除</button>
              </div>
            </div>
          </div>

          <!-- 芝士 working indicator (Slack-style: no token streaming). Shown
             from summon until every explicitly active turn finishes; the live
             working-log checklist stays visible for the whole turn. -->
          <div v-if="awaitingReply || todoItems.length" class="im-row">
            <div class="im-gutter">
              <CheeseAvatar :size="28" :name="agentName" />
            </div>
            <div class="im-main">
              <div class="im-meta">
                <span class="im-name">{{ agentName }}</span>
              </div>

              <!-- Working-log checklist (芝士's tasks, §3.1.1). Live during a
                 turn; between turns this is the topic's stored 进度层 (#187),
                 labelled so a leftover 进行中 row is not read as "running right
                 now". -->
              <div v-if="todoItems.length && todoRestored" class="todo-label">上次的进度</div>
              <ul v-if="todoItems.length" class="todo-list">
                <li v-for="t in todoItems" :key="t.id" class="todo-item" :class="'todo-' + t.status">
                  <v-icon class="todo-mark" size="14">{{ todoIcon(t.status) }}</v-icon>
                  <span class="todo-text">{{ t.subject }}</span>
                </li>
              </ul>

              <!-- Instant ack before the first message / during cold start -->
              <div v-if="awaitingReply" class="im-text">
                <span class="text-medium-emphasis">{{ agentName }}正在处理…</span>
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
      <AgentControls v-if="topic" :topic-id="topic.id" :active="true" questions-only />
      <template v-if="showComposer">
        <div
          class="composer pa-2 px-3"
          :class="{ 'composer--drop': dragOver }"
          @dragenter.prevent="dragOver = true"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop="onDropFiles"
        >
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
              <span v-if="mm.agent" class="mention-agent-badge">AI 队友</span>
              <span class="mention-menu-sub">{{ mm.sub }}</span>
              <span v-if="i === 0" class="mention-menu-hint">Enter</span>
            </button>
          </div>
          <!-- 输入区是一个控件，不是浮在页面上的几个零件：一个圆角描边的盒子把
               「待发的图片 + 输入框 + 动作」框成一块。盒子自己就是和时间线之间的
               分隔，所以上面那条 divider 没了。 -->
          <div class="composer-box">
            <!-- 图片输入: images waiting to go with the next send. -->
            <div v-if="pendingAtts.length || attsUploading" class="att-strip">
              <div v-for="(a, i) in pendingAtts" :key="a.path" class="att-thumb">
                <img v-if="a.mime.startsWith('image/')" :src="attachmentRawUrl(topic.id, a.path)" :alt="a.path" />
                <v-chip
                  v-else
                  variant="tonal"
                  class="pe-6"
                  prepend-icon="mdi-file-document-outline"
                  :title="a.path.split('/').pop()"
                >
                  <span class="text-truncate">{{ a.path.split('/').pop() }}</span>
                </v-chip>
                <button type="button" class="att-remove" title="移除" @click="removePendingAtt(i)">
                  <v-icon size="12">mdi-close</v-icon>
                </button>
              </div>
              <v-progress-circular v-if="attsUploading" indeterminate size="18" width="2" />
            </div>
            <!-- 输入框独占一整行。它旁边并排放按钮时，真正能打字的那块在手机上只剩
               半屏——而按钮的数量只会往上加。 -->
            <v-textarea
              ref="composerInput"
              v-model="draft"
              autocomplete="off"
              variant="plain"
              rows="1"
              auto-grow
              max-rows="6"
              hide-details
              density="comfortable"
              class="composer-input"
              :placeholder="composerHint"
              :title="
                enterSends
                  ? `Enter 发送，Shift+Enter 换行，⌘/Ctrl+Enter 发送并交给${agentName}，可直接粘贴图片`
                  : '可直接粘贴图片'
              "
              @keydown="onComposerKey"
              @paste="onComposerPaste"
              @compositionstart="onCompositionStart"
              @compositionend="onCompositionEnd"
            />
            <!-- 下面一行：动作靠左，发送靠右。发送是这一行唯一的主操作，所以它是
               唯一的实心按钮，其余一律是安静的图标。 -->
            <div class="composer-actions d-flex align-center ga-1">
              <!-- 这两个 input 是藏起来的，但**不能**用 display:none / visibility:hidden：
                   iOS Safari 拒绝用脚本打开一个被隐藏掉的文件选择框，按钮点下去
                   毫无反应。所以按 .visually-hidden 的老办法藏——留在布局里、只是
                   看不见。旁边 components/common/FileSelect.vue 里也是这么藏的。 -->
              <input ref="fileInput" type="file" multiple class="visually-hidden" @change="onFilePicked" />
              <input
                ref="imageInput"
                type="file"
                accept="image/*"
                multiple
                class="visually-hidden"
                @change="onFilePicked"
              />
              <!-- 附件上传走的是 HTTP，和聊天那条 socket 是两回事：socket 断着的
                 时候图片照样传得上去，所以这里不跟着 `connected` 一起禁用。 -->
              <v-btn
                class="composer-icon"
                icon="mdi-paperclip"
                variant="text"
                size="small"
                color="medium-emphasis"
                title="上传文件（每个最大 10MB）"
                @click="pickFiles"
              />
              <!-- 手机上多一颗「照片」：那儿没有截图可贴、也没有东西可拖，从文件
                   选择器里翻相册要绕好几步。 -->
              <v-btn
                v-if="!mdAndUp"
                class="composer-icon"
                icon="mdi-image-outline"
                variant="text"
                size="small"
                color="medium-emphasis"
                title="发送照片"
                @click="pickImages"
              />
              <v-spacer />
              <!-- 算力说的是「这条消息会在哪儿跑」，属于发送这一侧，不和左边那两个
                 「这条消息本身」的动作并列。它是设置不是动作，所以最安静。 -->
              <slot name="composer-chips" />
              <!-- 「交给芝士」：它不是一个自己存着状态的开关，它是正文的镜子——
                 点一下把 @ 写进输入框（你看得见、也能自己删），手打 @ 它就自己
                 亮。一个能和正文说不一样的话的开关（亮着、正文里却没有 @），会
                 让「这条到底算不算叫了它」变成没人答得上来的问题。 -->
              <button
                v-if="!alwaysSummon"
                type="button"
                class="summon-btn"
                :class="{ 'summon-btn--on': summonOn }"
                :disabled="!summonReady"
                :aria-pressed="summonOn"
                :title="
                  summonOn
                    ? `正文里已经 @ 了${agentName}，点这里取消`
                    : `交给${agentName}（也可以直接按 ⌘/Ctrl+Enter 发送并交给它）`
                "
                @click="toggleSummon"
              >
                <v-icon size="14">mdi-at</v-icon>
                <span class="summon-btn-label">交给{{ agentName }}</span>
              </button>
              <!-- 断线时照样能发：消息进发件箱、立刻显示，连上就自己走 (§14.1)。
                 按 `connected` 禁用会把「打字」和「后端此刻在不在」绑在一起。 -->
              <v-btn
                class="composer-send"
                color="primary"
                variant="flat"
                icon="mdi-send"
                size="small"
                title="发送"
                :disabled="attsUploading || (!draft.trim() && !pendingAtts.length)"
                @click="sendDraft()"
              />
            </div>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* ---------------------------------------------------------------------------
   平台行 —— 时间线上除了「人说的话」以外的一切，共用这一种形态。
   之前这里有五套几何：居中淡行、居中动作行、16px 起的折叠卡、16px 起的报错卡、
   还有一张 12px 圆角带渐变的事故卡。同一列里三条不同的左边缘（16 / 54 / 居中）
   是它读起来乱的直接原因。现在只有一条轴：和消息正文对齐的 54px
   （.im-row 的 16px padding + 28px 头像槽 + 10px gap）。
   严重度只改**记号和底色**，绝不改形态。
   --------------------------------------------------------------------------- */
.sys-row {
  padding: 3px 16px 3px 54px;
  font-size: 13px; /* 13px 是可读下限；平台行比正文低一档，不低于它 */
  line-height: 1.6;
  color: var(--muted);
}
/* 分栏之下，「谁都没说这句话」需要自己的位置：一行字的平台行居中（飞书/微信
   的通行做法）。**只有单行的那两种**——带右侧归属/状态列的动作卡、可折叠的
   报错卡、事故卡仍然留在左轴上：把一张右侧有状态列的卡居中，那一列就没了落点。 */
.sys-row.turn-summary,
.sys-row.im-event {
  padding-left: 16px;
}
.sys-row.turn-summary .sys-line,
.sys-row.im-event .sys-line {
  justify-content: center;
}
.sys-row.turn-summary .sys-text,
.sys-row.im-event .sys-text {
  flex: 0 1 auto;
}
details.sys-row > summary {
  cursor: pointer;
  list-style: none;
}
details.sys-row > summary::-webkit-details-marker {
  display: none;
}
.sys-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
/* 记号槽：宽度固定，所以圆点和图标不会把文字推成两个起点。 */
.sys-mark {
  flex: none;
  width: 15px;
  align-self: center;
  color: var(--faint);
}
.sys-mark--dot {
  position: relative;
  height: 15px;
}
.sys-mark--dot::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 4px;
  width: 6px;
  height: 6px;
  margin-top: -3px;
  border-radius: 50%;
  background: currentcolor;
}
.sys-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
/* 右侧固定放「归属 / 状态」和「动作」——扫一列就知道有没有在等自己。 */
.sys-who {
  flex: none;
  font-size: 12px;
  color: var(--faint);
}
.sys-count {
  flex: none;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.sys-action {
  flex: none;
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: var(--accent-ink); /* 记号色做文字对比度不够，见 design-system §1.6 */
  cursor: pointer;
}
.sys-action:hover {
  text-decoration: underline;
}
.sys-sub {
  padding-left: 23px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.sys-fold,
.sys-more {
  padding-left: 23px;
}
.sys-more > summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--faint);
}
.sys-meta {
  font-size: 12px;
  color: var(--faint);
  margin-top: 4px;
}
.doc-edit-diff {
  margin-top: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  font-size: 13px;
  color: var(--text);
}
.doc-edit-line {
  display: flex;
  gap: 8px;
  padding: 4px 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-edit-mark {
  flex: 0 0 1em;
}
.doc-edit-line--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.doc-edit-line--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.sys-detail {
  margin: 4px 0 6px;
  padding: 8px 10px;
  max-height: 240px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text);
}
.sys-occurrence + .sys-occurrence {
  border-top: 1px solid var(--line);
  padding-top: 4px;
}
/* 拖文件进来时的落区，只描一圈，不改布局（改了会把输入框顶一下）。 */
.composer--drop {
  outline: 1px dashed var(--accent);
  outline-offset: -3px;
}
/* 发件箱: 已显示、还没落库。淡一档，不换形状——它就是那条消息。 */
.im-row--pending .im-text,
.im-row--pending .im-name {
  opacity: 0.62;
}
.outbox-actions {
  display: flex;
  gap: 10px;
  margin-top: 2px;
}
.outbox-act {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: var(--accent-ink);
  cursor: pointer;
}
.outbox-act:hover {
  text-decoration: underline;
}
/* 本轮摘要：文件清单是次要信息，压到元信息档，一行放不下就截断。 */
.sys-files {
  font-size: 12px;
  color: var(--faint);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sys-sep {
  color: var(--faint);
}
/* 两档色，底色一律取自色板的 -wash，不再手搓 color-mix。 */
.sys-row--warn {
  background: var(--warn-wash);
}
.sys-row--warn .sys-mark {
  color: var(--warn);
}
.sys-row--danger {
  background: var(--danger-wash);
}
.sys-row--danger .sys-mark {
  color: var(--danger);
}
.sys-row--warn,
.sys-row--danger {
  padding-top: 6px;
  padding-bottom: 6px;
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
/* Working-log checklist (§3.1.1) — process, sits above the streaming text.
   Between turns the same list shows the stored 进度层 (#187) under a label. */
.todo-label {
  font-size: 12px;
  color: var(--muted);
  margin: 2px 0 0;
}
/* 任务清单块：强调靠 wash 底色，不靠左竖条（左条纹只留给引用块和结构线）。 */
.todo-list {
  list-style: none;
  margin: 2px 0 6px;
  padding: 6px 10px;
  background: rgba(var(--v-theme-primary), 0.05);
  border-radius: var(--radius-sm);
}
.todo-item {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  font-size: 13px;
  line-height: 1.5;
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
  color: var(--accent-ink);
  font-weight: 600;
}
.todo-completed {
  color: var(--faint);
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
  /* 手机底部那一条圆角/横杠区（安全区）会压在输入框上。桌面上这个值是 0。 */
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
}
/* 输入区是一个控件。原来输入框和几颗按钮各自浮在页面上，读起来是几个零件而不是
   一件东西——一个圆角描边就把它们收成一块，顺带替掉了上面那条 divider。 */
.composer-box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition: border-color 0.12s ease;
}
/* 聚焦时那条边只提一档：--muted 是正文级的灰，一压就把整个盒子变成了主角。 */
.composer-box:focus-within {
  border-color: var(--faint);
}
.composer-input :deep(textarea) {
  font-size: 14px;
  line-height: 1.5;
}
/* Vuetify 给输入框留的顶部内边距是「浮动标签落下来时站的地方」：plain + comfortable
   下是 15px 的 --v-input-padding-top 再加 3.5px，而底部只有 3px。这个输入框没有
   标签，那 15px 就只剩一个效果——第一行字被顶到盒子中间，上下差了六倍。 */
.composer-input :deep(.v-field) {
  --v-input-padding-top: 0px;
}
/* 那道遮罩是给「文字从浮动标签底下滑过去」用的渐隐。没有标签就没有要遮的东西，
   留着它只会把第一行字的上半截淡掉——尤其在内边距刚被收窄之后。 */
.composer-input :deep(.v-field__input) {
  -webkit-mask-image: none;
  mask-image: none;
}
/* 动作行的规矩，三条：
   1. 一行一个高度。原来是 24 / 32 / 24 / 30 四种，这是它看起来像一堆零件的主因。
   2. 静止时谁也不画边框、不画底色——状态用墨色说，不用盒子说。
   3. 整行只有一个实心块，就是发送。
   位置负责表达语义：左边是「这条消息本身」的动作，右边是「它会怎么发出去」。 */
.composer-actions {
  min-height: 28px;
  margin-top: 2px;
}
.composer-icon,
.composer-send {
  width: 28px;
  height: 28px;
}
/* 「交给芝士」。它和发送并排，但绝不能也是实心琥珀——一行里只有一个实心块，
   那个位置是发送的。亮起来只改一条描边和墨色，形态不变。 */
.summon-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: 13px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  transition:
    color 0.12s ease,
    border-color 0.12s ease,
    background-color 0.12s ease;
}
.summon-btn:hover:not(:disabled) {
  background: var(--fill);
  color: var(--text);
}
.summon-btn:disabled {
  cursor: default;
  opacity: 0.5;
}
.summon-btn--on {
  border-color: var(--accent);
  color: var(--accent-ink);
}
/* 窄屏上只留那个 @ 图标：这一行右边还站着算力和发送，三个都带字就换行了。 */
@media (max-width: 480px) {
  .summon-btn-label {
    display: none;
  }
}

/* 忘了 @ 的补救行。它属于那条消息（和正文左对齐），不是一条平台行——平台行说的
   是平台做了什么，这一行说的是**你**还差一步。 */
.summon-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 13px;
  color: var(--faint);
}
.summon-hint-btn {
  padding: 2px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
}
.summon-hint-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent-ink);
}
.summon-hint-btn:disabled {
  cursor: default;
  opacity: 0.6;
}

/* @-autocomplete popup — mirrors TopicView's composer picker. */
.mention-menu {
  display: flex;
  flex-direction: column;
  margin-bottom: 6px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.mention-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  min-height: 36px;
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.mention-menu-item:hover {
  background: var(--fill);
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
  /* Theme-invariant pair (same call as the default avatar in LeftAppRail): the
     slate disc is one value in both themes, so its ink must be too. */
  color: #fff;
  background: #8a94a3;
  flex: none;
}
.mention-avatar--agent {
  /* 琥珀底上的墨：主题色自己那一套，深浅主题各有一个值。 */
  color: rgb(var(--v-theme-on-primary));
  background: var(--accent);
}
.mention-avatar--broadcast {
  /* --ink inverts with the theme (near-black → near-white), so the ink on it
     has to invert too; --surface is #fff in light (unchanged) and #1B1D20 dark. */
  color: var(--surface);
  background: var(--ink);
}
.mention-avatar--topic {
  background: var(--fill);
  color: var(--muted);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  /* 记号色做文字对比度不够 (design-system §1.6): --accent 在白底上是 2.65:1,
     远低于正文门槛 4.5;--accent-ink 是 5.76:1。 */
  color: var(--accent-ink);
  background: var(--accent-wash);
}
.mention-menu-sub {
  font-size: 0.75rem;
  color: var(--faint);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--faint);
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
/* 悬停只改颜色不改位置（设计系统 §9.1）。改的是气泡自己那一档 —— 原来刷的是
   整行的 --fill，而气泡也是 --fill，鼠标扫过去气泡就消失了。 */
.im-row:hover .im-text {
  background: var(--fill-2);
}
.im-row--self:hover .im-text {
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
  /* Literal on purpose: the real ground is avatarColor() (a fixed hsl bound
     inline in the template), identical in both themes — so is the initial. */
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  border-radius: 8px;
}
/* 真头像：同一个 28px 方槽，图片裁进去，不撑变消息行。 */
.im-avatar--photo {
  object-fit: cover;
  background: var(--fill);
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
  font-size: 12px; /* 12 是元信息档；11.5 既不在档位上，也在可读下限以下 */
  color: var(--faint);
}
/* ---- 分栏气泡 (2026-09-09, <@符露夀> 定) ----
   一条消息是一个气泡，我说的靠右、别人和芝士靠左。三件事一起说明「是不是我」：
   位置、头像在哪边、尖角朝哪边 —— **不能**用颜色说，别家那一格放的是品牌色，
   而我们这套色板里那个位置是琥珀，按设计系统只留给主操作、激活态和品牌。所以
   两侧只差一档灰。
   立面靠描边不靠填充（设计系统 §3.4「卡片只描边，不投影」）：--fill 在白底上
   只差 3% 亮度，那是「悬停高亮」那一档的强度，单靠它立不起一个面。--line-2 而
   不是 --line：--line 比 --fill 还浅，描在 --fill 的面上等于没描。 */
.im-text {
  display: inline-block;
  max-width: 100%;
  padding: 7px 12px;
  font-size: 14px;
  line-height: 1.62;
  color: var(--text);
  word-break: break-word;
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  border-top-left-radius: var(--radius-sm); /* 尖角朝说话的那一边 */
}
/* 对侧留白。各家常见的是 15%，这里 8% —— 头像在哪边本身已经说明了侧，不需要
   那么大的空档，省下的宽度还给正文（默认栏宽下 403px 对 365px）。 */
.im-row {
  padding-right: calc(16px + 8%);
}
.im-row--self {
  flex-direction: row-reverse;
  padding-right: 16px;
  padding-left: calc(16px + 8%);
}
.im-row--self .im-meta {
  flex-direction: row-reverse;
}
/* 自己那一侧的所有块级内容（气泡、图片、附件、表情、提示）一起靠右。 */
.im-row--self .im-main {
  text-align: right;
}
.im-row--self .im-text {
  text-align: left; /* 气泡靠右，气泡里的字仍然左起 */
  background: var(--fill-2);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-sm);
}
/* 同一个人连着说的第二条：尖角收掉，两条读成一段。分栏之下这是「连续消息」
   唯一还剩的信号 —— 位置已经被拿去表示「是不是我」了。 */
.im-row--cont .im-text {
  border-top-left-radius: var(--radius-lg);
}
.im-row--self.im-row--cont .im-text {
  border-top-right-radius: var(--radius-lg);
}
/* 气泡里的行内元素（表情 chip、选项、提示）跟着靠右。 */
.im-row--self .rx-row,
.im-row--self .ask-row,
.im-row--self .summon-hint {
  justify-content: flex-end;
}
/* 悬停条镜像到左上角：自己那侧的右上角被气泡的尖角占着。 */
.im-row--self .im-actions {
  right: auto;
  left: 12px;
}
/* 表情面板挂在悬停条上，所以它也得跟着换边 —— 不换的话它从条的右端往右展开，
   而条已经在这一列的最左边，面板整个滑出聊天栏、盖到侧栏上去（实测点不到）。 */
.im-row--self .rx-picker {
  right: auto;
  left: 0;
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
.im-file-link,
.att-thumb,
.att-thumb .v-chip {
  max-width: 100%;
}
.im-file-link :deep(.v-btn__content) {
  min-width: 0;
}
/* Pending attachments, each with a remove button. */
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
.att-thumb .v-chip {
  line-height: normal;
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
  display: inline-flex;
  position: absolute;
  top: -6px;
  right: -6px;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
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
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
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
  color: var(--muted);
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
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.im-upgraded:hover {
  background: var(--surface);
  border-color: var(--accent);
}
/* @mention: neutral inset, ink text — not amber. */
.im-text :deep(.mention) {
  color: var(--accent-ink);
  background: var(--fill);
  border-radius: var(--radius-sm);
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
/* @person handle reads as a link: persistent accent underline. File/topic
   refs (file icon / #) keep their chip look and only underline on hover. */
.im-text :deep(.mention:not(.file-ref):not(.topic-ref)) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
.im-text :deep(.mention:hover) {
  text-decoration: underline;
}
/* 文件 chip 前的 mdi 图标。不挂在 .im-text 下：同样的 chip 也出现在动作卡
   (.action-verb) 和系统事件行里，那两处不在 .im-text 里面。 */
:deep(.file-ref__icon) {
  margin-right: 3px;
  font-size: 0.92em;
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
  box-shadow: var(--shadow-1);
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
  box-shadow: var(--shadow-2);
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
  border-radius: var(--radius-md);
  padding: 5px 14px;
  font-size: 13px;
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
  border-radius: var(--radius-pill);
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

@keyframes incident-pulse {
  50% {
    box-shadow: 0 0 0 6px transparent;
  }
}
@media (prefers-reduced-motion: reduce) {
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
/* 气泡里那一层往回走到「面」那一级配一条更浅的线：一层比一层亮，和两侧的气泡
   底色（--fill / --fill-2）都分得开。留在 --fill 的话它和左侧气泡同色，糊成一块。 */
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--surface);
  border: 1px solid var(--line);
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
