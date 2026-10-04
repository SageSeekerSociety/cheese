<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 会话的控制条，加上重建出来的 transcript 时间线。
import type { AgentControlState, Block, Topic } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { computed, nextTick, onMounted, onUpdated, ref, watch } from 'vue'

import { getTranscript, SITE_PAGE_SIZE } from '../../api'
import { useStickToBottom } from '../../composables/useStickToBottom'
import { isAgentBlock, isAgentHandle } from '../../lib/authorship'
import { scrollTopAfterPrepend, shouldLoadOlder } from '../../lib/blockPaging'
import { renderMarkdown } from '../../lib/renderMessage'
import {
  countLines,
  eventArg,
  eventFailed,
  eventVerb,
  formatSpan,
  groupByTurn,
  isLongSiteEntry,
  isNarration,
  middleTruncate,
  overflowsClamp,
  shouldKeepPinning,
  SITE_CLAMP_LINES,
} from '../../lib/siteLog'
import { isPlatformEvent } from '../../lib/toolLabels'
import CheeseAvatar from '../CheeseAvatar.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MemberActivity from '../room/MemberActivity.vue'
import SessionInspector from '../SessionInspector.vue'

import SiteStepOutput from './SiteStepOutput.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // This tab is the one on screen. Load happens on the rising edge, exactly
    // like opening the old drawer did.
    active?: boolean
    // 房间名册 handle → 名字。这一栏给每一行署的是它的作者，和对话栏一个规矩：
    // 一个房间可以先后交给两个队友，各自的话各自署名，不能写死「芝士」。
    memberNames?: Record<string, string>
    // 这个房间现在有没有活在跑。现场自己听不到轮次帧（WS 在对话栏那边），而
    // 「最后一组还没完」和「最后一组是上一轮留下的」看起来一模一样。
    working?: boolean
    // 房间 socket 上最近一帧会话状态（对话栏收到，经 TopicView 转过来）。
    agentControl?: AgentControlState | null
    // 在跑的轮次 id → 开始时间（毫秒），对话栏从 socket 上算的。哪一组「进行中」读它。
    runningTurns?: Record<string, number>
    // 一轮结束时加一：趁这时把这一段安静地重读一遍，补上 socket 断开时漏掉的行。
    refreshTick?: number
    /** 名册里查不到名字的 AI 发言按这个名字称呼（项目 AI 队友的名字）。 */
    agentName?: string
    /** 此刻谁在这个房间里忙（`MemberActivity` 那一份）。 */
    activity?: MemberActivityLine[]
  }>(),
  {
    active: false,
    memberNames: () => ({}),
    working: false,
    agentControl: null,
    agentName: () => t('work.room.defaultAgentName'),
    activity: () => [],
  }
)

const emit = defineEmits<{
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', id: string): void
  (e: 'mention-click', handle: string): void
}>()

const loading = ref(false)
const errorMsg = ref<string | null>(null)

// 现场: read-only transcript timeline. 手上这一窗是**当前视角**的：「全部」是房间里
// 所有人的，「只看某个队友」时只有它自己的——过滤在服务端的分页里做，不是在读到
// 的一页上再挑（那正是「切过去就等于加载全部」的来源）。
const transcript = ref<Block[]>([])
// 更早的现场还在库里没拉。和对话栏一样，只在读的人自己往上翻时才拉。
const hasOlder = ref(false)
const loadingOlder = ref(false)
// 每一轮从什么时候开始（毫秒），读到的每一页都带着它那几轮的。组头的用时从这里算起。
const turnStarts = ref<Record<string, number>>({})

function noteStarts(starts: Record<string, string> | undefined): void {
  const parsed = Object.entries(starts ?? {}).map(([id, at]) => [id, Date.parse(at)] as const)
  const fresh = parsed.filter(([id, at]) => Number.isFinite(at) && turnStarts.value[id] !== at)
  if (fresh.length) turnStarts.value = { ...turnStarts.value, ...Object.fromEntries(fresh) }
}

// 手上这一窗是给哪个视角读的（null = 全部）。换视角时它和这一栏现在要读的东西就
// 不是一回事了，重读时不能再和它合。
let loadedFor: string | null = null

async function loadOlder() {
  const tid = props.topic?.id
  const oldest = transcript.value[0]
  if (!tid || !oldest || loadingOlder.value || !hasOlder.value) return
  const author = viewing.value
  loadingOlder.value = true
  const el = scrollRef.value
  const before = el ? { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight } : null
  try {
    const page = await getTranscript(tid, {
      limit: SITE_PAGE_SIZE,
      before: oldest.id,
      author,
    })
    // 读的人可能已经换了视角：这一页是上一位的，丢掉——合进来就是把别人的行塞进
    // 这个人的时间线。
    if (props.topic?.id !== tid || viewing.value !== author) return
    noteAgents(page.data)
    noteStarts(page.turn_starts)
    transcript.value = [...page.data, ...transcript.value]
    hasOlder.value = page.has_more === true
    // Prepending grows the content ABOVE the viewport; without this the reader
    // is thrown backwards by exactly that much (same fix as the chat's pager).
    await nextTick()
    const sc = scrollRef.value
    if (sc && before) sc.scrollTop = scrollTopAfterPrepend(before, sc.scrollHeight)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : t('work.room.site.loadFailed')
  } finally {
    loadingOlder.value = false
  }
}

// 和对话栏同一个判据：离顶还有一屏就开始拉上一页，已经有的人不用等。
function onSiteScroll() {
  const el = scrollRef.value
  if (el && shouldLoadOlder(el.scrollTop, { hasMore: hasOlder.value, loading: loadingOlder.value })) {
    void loadOlder()
  }
}
// The scroll container, so the timeline can open on its newest entry the way a
// chat log does. Measured before this existed: opening 现场 left scrollTop at 0
// with a scrollHeight of 1818 and a viewport of 500 — the reader landed 1300px
// above the thing they came to see.
const scrollRef = ref<HTMLElement | null>(null)
useStickToBottom(scrollRef, 48)
// Entries the reader has expanded. Keyed by block id, and deliberately NOT
// reset when the transcript refreshes: a silent refresh re-collapsing what
// someone just opened is the same bug as scrolling them away from it.
const expandedSite = ref<Set<string>>(new Set())

function toggleSiteEntry(id: string): void {
  const next = new Set(expandedSite.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedSite.value = next
}

// Which rendered entries actually overflow the 12-line clamp. Measured from the
// DOM rather than guessed from the text: `isLongSiteEntry` cannot know how wide
// this panel is, so at a wide width it calls a 900-character paragraph long and
// clamps nothing (展开 then opens nothing), and at a narrow width a modest
// paragraph can overflow with no button to open it. `scrollHeight` on an
// `overflow: hidden` box is still the full content height, so this reads the
// true height whether or not the clamp is applied.
const overflowing = ref<Set<string>>(new Set())
// Once a real measurement has happened, its answer replaces the content
// heuristic. In a unit test there is no layout, so this stays false and the
// heuristic keeps the template deterministic.
const measured = ref(false)

function measureBodies(): void {
  const root = scrollRef.value
  // A hidden panel (a tab that is not on screen) has no height to measure.
  // Measuring it would read every scrollHeight as 0 and wrongly clear every
  // clamp; leaving `measured` false keeps the heuristic until it is shown.
  if (!root || root.clientHeight === 0) return
  const next = new Set<string>()
  let sawLayout = false
  for (const el of root.querySelectorAll<HTMLElement>('.site-msg__body[data-site-body]')) {
    const id = el.dataset.siteBody
    if (!id) continue
    const lineHeight = Number.parseFloat(getComputedStyle(el).lineHeight)
    if (!Number.isFinite(lineHeight) || lineHeight <= 0) continue
    sawLayout = true
    if (overflowsClamp(el.scrollHeight, lineHeight)) next.add(id)
  }
  if (!sawLayout) return
  measured.value = true
  // Replace only when the membership changed, so the measurement cannot drive
  // its own re-render (`onUpdated`) forever.
  if (next.size !== overflowing.value.size || [...next].some((id) => !overflowing.value.has(id))) {
    overflowing.value = next
  }
}

onMounted(measureBodies)
onUpdated(measureBodies)

// Long enough to be clamped: the measured answer once we have it, the content
// heuristic until then. Both answer the same question, so the 展开 button and
// the clamp never disagree about which entries are long.
function isLong(b: Block): boolean {
  return measured.value ? overflowing.value.has(b.id) : isLongSiteEntry(b.content)
}

// 参数收起时显示的这一份：路径和命令从中间省——两头才是认得出它的那半截，从尾部
// 省正好把文件名剪掉；散文仍从尾部省（css 的 ellipsis），中间的省略号会把它拦腰
// 截断。摊开时模板走 eventDetail，不经过这里。
function argDisplay(b: Block): string {
  const arg = eventArg(b)
  return argIsProse(b) ? arg : middleTruncate(arg)
}

// A single `scrollTop = scrollHeight` at nextTick does NOT work here, which is
// how this shipped broken the first time: the panel renders its spinner first,
// so the container is one viewport tall with nothing to scroll, the assignment
// clamps to 0, and the timeline lays out underneath — leaving the reader on the
// oldest entry, the exact bug this exists to fix. Measured on the deployed page:
// scrollHeight 500 at +40ms, 2066 at +120ms, scrollTop 0 throughout.
// So keep re-pinning while the height is still moving (see shouldKeepPinning).
function scrollSiteToTail(): void {
  let lastHeight = -1
  let frames = 0
  const pin = (): void => {
    const el = scrollRef.value
    if (!el) return
    el.scrollTop = el.scrollHeight
    if (shouldKeepPinning(el.scrollHeight, lastHeight, frames)) {
      lastHeight = el.scrollHeight
      frames += 1
      requestAnimationFrame(pin)
    }
  }
  nextTick(() => requestAnimationFrame(pin))
}

// 读的人是不是正看着最底下。新的一行来了，只有这时才跟着往下走；往上翻着看旧记录
// 的人，不能被一行新的拽回底部。
function atTail(): boolean {
  const el = scrollRef.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 48
}

// 这一栏已经有东西时，再读一遍是安静的：不换骨架屏、不跳回底部——换成骨架屏再
// 换回来，就是整栏闪一下。
//
// 每次读的都是**当前视角**的最近一页，和打开时一样。换了视角就是换了一个人的
// 时间线：那一页到手之前手上那份还是上一位的（照旧渲染，不闪骨架屏），到手之后
// 它整个换掉——不是往上摞。一个人可能有上千步，把上一位翻出来的历史带过去，就
// 等于又「切一次加载全部」。
async function load() {
  const tid = props.topic?.id
  if (!tid) return
  const author = viewing.value
  const switched = author !== loadedFor
  const quiet = transcript.value.length > 0
  const follow = switched || !quiet || atTail()
  if (!quiet) loading.value = true
  errorMsg.value = null
  try {
    const tx = await getTranscript(tid, { limit: SITE_PAGE_SIZE, author })
    // 读的人可能已经又换了一个视角：这一页不是他现在要的，丢掉。骨架屏由下面的
    // finally 收掉，不能让它留在半路。
    if (props.topic?.id !== tid || viewing.value !== author) return
    loadedFor = author
    noteAgents(tx.data)
    noteStarts(tx.turn_starts)
    transcript.value = mergeSite(tx.data, transcript.value, switched)
    if (switched || !quiet) hasOlder.value = tx.has_more === true
    // Follow the tail on every open of a topic's 现场 — that is what "open on
    // the newest" means. 换视角也是「打开这个人的现场」，同样停在最新的那一条。
    if (follow) scrollSiteToTail()
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : t('work.room.site.loadFailed')
  } finally {
    loading.value = false
  }
}

// 一页读回来的，和这一栏手上已有的，按 id 合成一份。读的请求在路上的时候 socket
// 又送来了几行，它们比这一页新：留着，接在后面。手上那份里比这一页最老的一行还
// 老的（往上翻出来的更早的记录），也留着。
//
// `switched` 是另一回事：手上那份是上一个视角的，它的「更早」不是这个人的更早。
// 那时只留请求在路上时新到的几行 —— 比这一页最新的一条还新的，是真的还没进这一页
// 的几步，不是翻出来的历史。
function mergeSite(page: Block[], held: Block[], switched = false): Block[] {
  if (!page.length) return held.length ? held : page
  const ids = new Set(page.map((b) => b.id))
  const first = Date.parse(page[0].created_at)
  const last = Date.parse(page[page.length - 1].created_at)
  const older = switched ? [] : held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) < first)
  const newer = held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) >= last)
  return [...older, ...page, ...newer]
}

/**
 * socket 上来的一行（对话栏收到，一路转过来）：新的接在末尾，已有的原地换掉。
 * 别的话题的、分身卡上的、不是事件的，都不归这一栏。只看一个队友时，别人的行
 * 也不归这一栏——手上这条时间线就是那个人的。
 */
function receive(block: Block): void {
  if (block.topic_id !== props.topic?.id || block.kind !== 'event' || block.task_id) return
  // 名册先记上：别的队友在干活，tab 得出现（切过去时才读得到它的记录）。
  noteAgents([block])
  if (viewing.value !== null && block.author !== viewing.value) return
  const at = transcript.value.findIndex((b) => b.id === block.id)
  if (at >= 0) {
    const next = transcript.value.slice()
    next[at] = block
    transcript.value = next
    return
  }
  const follow = atTail()
  const time = Date.parse(block.created_at)
  const next = transcript.value.slice()
  // 几乎总是接在末尾；偶尔晚到的一行按时间插回它的位置（一轮以它记下的时间排）。
  let i = next.length
  while (i > 0 && Date.parse(next[i - 1].created_at) > time) i -= 1
  next.splice(i, 0, block)
  transcript.value = next
  if (follow) scrollSiteToTail()
}

defineExpose({ receive })

// ---- 按队友看 ----
// 一个房间可以先后、甚至同时交给几个队友。时间线是他们交错着的，而人来看的往往
// 是其中一个在干什么。作者就是做这一步的那个队友：做过一步、说过一句的参与者。
// 平台自己的话（署名 system）和人的动作只在「全部」里。
//
// 这一排 tab 是**攒出来**的，不是每次从手上那一窗里现算的：只看一个队友时手上只有
// 那个人的行，现算的话这一排会当场塌成一个 tab，人就切不回去了。每读一页都往里
// 记新露面的（从新到旧地翻，一个都不会漏），socket 上来的一行也记。
const agents = ref<string[]>([])

function noteAgents(blocks: Block[]): void {
  const seen = new Set(agents.value)
  let grew = false
  const next = agents.value.slice()
  for (const b of blocks) {
    if (b.author_type !== 'participant' || seen.has(b.author)) continue
    if (!b.meta?.tool && !isNarration(b.meta)) continue
    seen.add(b.author)
    next.push(b.author)
    grew = true
  }
  if (grew) agents.value = next
}

// null = 全部。
const selectedAgent = ref<string | null>(null)
const viewing = computed(() =>
  selectedAgent.value !== null && agents.value.includes(selectedAgent.value) ? selectedAgent.value : null
)

// 换一个视角 = 换一条时间线：重读这个人的最近一页，停在最新的那一条。不是把手上
// 这一窗（可能是「全部」，也可能是上一位）就地滤一遍 —— 那既是「一次加载全部」，
// 也停在原地，而人是来看这个人刚刚在干什么的。
function selectAgent(next: string | null): void {
  if (next === selectedAgent.value) return
  selectedAgent.value = next
  void load()
}

const visible = computed(() =>
  viewing.value === null ? transcript.value : transcript.value.filter((b) => b.author === viewing.value)
)
// 「全部」下几个队友的轮次交错着排，组头不写是谁的，交错的两段就分不开。只看一个
// 队友、或者房间里只有一个时，名字是多余的。
function turnAuthor(entries: Block[]): string | null {
  if (viewing.value !== null || agents.value.length < 2) return null
  const who = entries.find((b) => agents.value.includes(b.author))
  return who ? who.author : null
}

function agentLabel(handle: string): string {
  return props.memberNames[handle] || (isAgentHandle(handle) ? props.agentName : handle)
}

// 此刻在干活的队友（对话栏从 socket 上学来，和输入框下面那一行是同一份）：只看
// 一个队友时只说它。
const workingLines = computed(() =>
  props.activity.filter((l) => l.kind === 'working' && (viewing.value === null || l.handle === viewing.value))
)

// Opening the tab loads it, exactly like opening the drawer used to. 它读的是
// 「现在看的是谁」，所以要等在 `viewing` 之后 —— immediate 的那一次是当场跑的。
watch(
  () => props.active,
  (on) => {
    if (on) void load()
  },
  { immediate: true }
)

// 一轮刚结束：开着的这一栏安静地重读一遍。
watch(
  () => props.refreshTick,
  () => {
    if (props.active) void load()
  }
)

function authorLabel(b: Block): string {
  // 同对话栏：名册上没有的 AI 作者显示成「芝士」，不把 handle 摆出来。
  return props.memberNames[b.author] || (isAgentBlock(b) ? props.agentName : b.author)
}

function fmtTime(iso: string): string {
  // Local HH:mm, not raw UTC slice.
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// 参数里有中文的（文档标题、验收卡标题、一句说明）不走等宽：中文没有等宽字形，
// 落在等宽字体上会掉到别的字体、字距被拉开。路径和命令照旧等宽。
const CJK = /[\u3400-\u9fff\uf900-\ufaff]/
function argIsProse(b: Block): boolean {
  return CJK.test(eventArg(b))
}

// 摊开这一行之后显示的那一份：参数原文，一个字都没剪。没有第二份时摊开的仍是
// 这一行本身 —— 面板窄到把它省略掉时，展开是唯一能看全的办法。
function eventDetail(b: Block): string {
  return b.meta?.detail || eventArg(b)
}
function eventError(b: Block): string {
  return b.meta?.error ?? ''
}
// 没有参数的那种行（平台提示、后端报错），整句就压在动词上。它会折到三行
// （见样式里的 `.site-act--solo`），全文挂到 title 上，鼠标停一下看全。
function soloVerb(b: Block): string {
  return eventArg(b) ? '' : eventVerb(b)
}
// 圆点分级: solid = platform action, faint = plain work (structured fields
// only — never guessed from the content text).
function eventPlatform(b: Block): boolean {
  return isPlatformEvent(b.meta, b.refs)
}

// 芝士自己说的话也走 kind=event —— 没有显式发布的输出就是这么落库的（后端
// `_persist_assistant_message` 的 as_progress 分支）。它不是一步操作，所以既不
// 按工具行渲染，也不计进步数。旧数据没有 meta，按原样走工具行那条路。
function isSay(b: Block): boolean {
  return b.kind !== 'event' || isNarration(b.meta)
}

// 芝士说的话按 markdown 渲染，和对话栏走同一条路（lib/renderMessage）。
// 这一栏原来是把原文摆出来（mono + pre-wrap，Claude Code 会话那种），但一段汇报
// 落到人眼里就是一堆星号和反引号，粗体、列表、代码块全丢了信息。引用 token 也照
// 对话栏展开成 chip —— 光看 `<@handle>` `<&path>` 是认不出人的。
const sayRefs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} }))

function renderSay(text: string): string {
  return renderMarkdown(text, sayRefs.value)
}

// chip 是 v-html 塞进来的，点击只能从容器上委派（同对话栏）。文件 chip 带上这条
// 消息自己的 task_id：现场读的是别的任务的记录时，路径要在那个任务的目录里找。
function onSayClick(event: MouseEvent, b: Block): void {
  const chip = (event.target as HTMLElement | null)?.closest('.mention') as HTMLElement | null
  if (!chip) return
  if (chip.dataset.handle) emit('mention-click', chip.dataset.handle)
  else if (chip.dataset.topic) emit('open-topic', chip.dataset.topic)
  else if (chip.dataset.file) emit('open-file', chip.dataset.file, b.task_id ?? null)
}

// 在跑的那一轮，这一页读回来时可能还没登记：对话栏从 socket 上知道它从什么时候开始。
// 记下来，这一轮停了、重读还没回来的那一会儿，用时也不缩回去。
watch(
  () => props.runningTurns,
  (running) => {
    const unseen = Object.entries(running ?? {}).filter(([id]) => !(id in turnStarts.value))
    if (unseen.length) turnStarts.value = { ...Object.fromEntries(unseen), ...turnStarts.value }
  },
  { immediate: true }
)
const turns = computed(() => groupByTurn(visible.value, turnStarts.value))

// 这一组还在跑吗：它的轮次在对话栏听到的在跑的轮次里。只看「房间有没有活」的话，
// 新一轮还没落下第一行时，上一轮的那一组会被说成进行中。
function isLive(index: number): boolean {
  return props.working && turns.value[index].key in (props.runningTurns ?? {})
}
</script>

<template>
  <div ref="scrollRef" class="panel-site" @scroll="onSiteScroll">
    <LoadingSkeleton v-if="loading" variant="site" :rows="5" />
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">
      {{ errorMsg }}
    </v-alert>

    <!-- read-only transcript timeline (芝士 messages + tool events) -->
    <template v-else>
      <SessionInspector v-if="topic" :topic-id="topic.id" :active="active" :pushed="agentControl" />
      <div v-if="agents.length > 1" class="site-agents" role="tablist">
        <button
          type="button"
          role="tab"
          class="site-agents__tab"
          :class="{ 'site-agents__tab--on': viewing === null }"
          :aria-selected="viewing === null"
          @click="selectAgent(null)"
        >
          {{ t('work.room.site.agents.all') }}
        </button>
        <button
          v-for="a in agents"
          :key="a"
          type="button"
          role="tab"
          class="site-agents__tab"
          :class="{ 'site-agents__tab--on': viewing === a }"
          :aria-selected="viewing === a"
          @click="selectAgent(a)"
        >
          {{ agentLabel(a) }}
        </button>
      </div>
      <MemberActivity :lines="workingLines" class="site-activity" />
      <div v-if="transcript.length === 0" class="text-center text-medium-emphasis py-6">
        {{ t('work.room.site.empty') }}
      </div>
      <div v-else class="site-log pa-3">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? t('work.room.site.loadingOlder') : t('work.room.site.older') }}
        </div>
        <section v-for="(turn, index) in turns" :key="turn.key" class="turn">
          <!-- 组头：这一轮从什么时候开始、几步、多久。触发这一轮的那句话在对话
               栏，现场读不到它（人写的块不带 turn_id），所以这里不写标题。 -->
          <div class="turn__head">
            <span v-if="turnAuthor(turn.entries)" class="turn__who" data-testid="turn-who">
              {{ agentLabel(turnAuthor(turn.entries)!) }}
            </span>
            <span class="turn__time">{{ fmtTime(turn.startedAt) }}</span>
            <span v-if="isLive(index)" class="turn__live">
              <i class="turn__pulse" />
              {{ t('work.room.site.live') }}
            </span>
            <span class="turn__meta">
              {{ t('work.room.site.steps', { count: turn.steps })
              }}<template v-if="turn.seconds > 0"> · {{ formatSpan(turn.seconds) }}</template>
            </span>
          </div>
          <template v-for="b in turn.entries" :key="b.id">
            <!-- 一步一行：动词成列，参数占满剩下的宽度，时间悬停才出现。参数太
                 长时截断而不是折行 —— 点这一行摊开全文。 -->
            <div
              v-if="!isSay(b)"
              class="site-act"
              :class="{
                'site-act--platform': eventPlatform(b),
                'site-act--failed': eventFailed(b),
                'site-act--solo': !eventArg(b),
              }"
            >
              <i class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(b) }" />
              <span class="site-act__verb" :title="soloVerb(b) || undefined">{{ eventVerb(b) }}</span>
              <button
                v-if="eventArg(b)"
                type="button"
                class="site-act__argtext"
                :class="{
                  'site-act__argtext--full': expandedSite.has(b.id),
                  'site-act__argtext--prose': argIsProse(b),
                }"
                data-testid="site-act-arg"
                :aria-expanded="expandedSite.has(b.id)"
                :title="eventArg(b)"
                @click="toggleSiteEntry(b.id)"
              >
                {{ expandedSite.has(b.id) ? eventDetail(b) : argDisplay(b) }}
              </button>
              <span v-else class="site-act__argtext"></span>
              <span class="site-act__time">{{ fmtTime(b.created_at) }}</span>
              <!-- 挂了的那一步：错误摘要另起一行，缩进到参数那一列，和上面对齐。
                   它自己也能点开：一个会话没起来时，这里是它启动时打印的原文，
                   而那一行没有参数可点。 -->
              <button
                v-if="eventFailed(b) && eventError(b)"
                type="button"
                class="site-act__error"
                :class="{ 'site-act__error--full': expandedSite.has(b.id) }"
                data-testid="site-act-error"
                :title="eventError(b)"
                @click="toggleSiteEntry(b.id)"
              >
                {{ eventError(b) }}
              </button>
              <!-- 摊开的这一步打印了什么：收着，点了才取。 -->
              <SiteStepOutput
                v-if="topic && expandedSite.has(b.id) && b.meta?.output_bytes"
                :topic-id="topic.id"
                :block-id="b.id"
                :bytes="b.meta.output_bytes"
              />
            </div>
            <!-- 芝士 speaks — shown as a person, with avatar (like the chat) -->
            <div v-else class="site-msg">
              <!-- 头像上的字和它右边写的名字同一个来源：这一行的作者。 -->
              <CheeseAvatar :size="26" :name="authorLabel(b)" :handle="b.author" class="site-msg__av" />
              <div class="site-msg__main">
                <div class="site-msg__meta">
                  <span class="site-msg__name">{{ authorLabel(b) }}</span>
                  <span class="t-meta">{{ fmtTime(b.created_at) }}</span>
                </div>
                <!-- 渲染成正文，不摆原文：现场读的也是人说的话，粗体、列表、代码块
                   和对话栏一个样子（走同一个 renderMarkdown）。 -->
                <!-- Clamp = max-height + a fade, NOT -webkit-line-clamp: that one
                    is ignored the moment the markdown holds a block-level <pre>,
                    so a 128-line code block rendered at full height and the
                    展开 button opened nothing. The fade sits on the wrapper,
                    because the body's own overflow:hidden would clip it. -->
                <div
                  class="site-msg__clip"
                  :class="{ 'site-msg__clip--clamped': isLong(b) && !expandedSite.has(b.id) }"
                >
                  <div
                    class="site-msg__body md-content"
                    :class="{ 'site-msg__body--clamped': isLong(b) && !expandedSite.has(b.id) }"
                    :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
                    :data-site-body="b.id"
                    @click="onSayClick($event, b)"
                    v-html="renderSay(b.content)"
                  />
                </div>
                <!-- 过长时不直接摊开：一条几千字的输出会把它前后的所有东西挤出
                   屏幕，而 现场 的价值恰恰是「一眼看完发生了什么」。折叠到 12
                   行，想看全的自己点开。 -->
                <button
                  v-if="isLong(b)"
                  type="button"
                  class="site-msg__more"
                  :aria-expanded="expandedSite.has(b.id)"
                  @click="toggleSiteEntry(b.id)"
                >
                  {{
                    expandedSite.has(b.id)
                      ? t('work.room.site.collapse')
                      : t('work.room.site.expandAll', { count: countLines(b.content) })
                  }}
                </button>
              </div>
            </div>
          </template>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 谁在干活，贴在这一栏的顶上：往上翻旧的记录时，它仍然说着此刻的事。 */
.site-activity {
  position: sticky;
  top: 0;
  z-index: var(--z-raised);
  padding-block: 8px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
/* 按队友看：一排文字按钮，选中的那个换底色和墨色，不用琥珀——这里不是主操作。 */
/* 钉在滚动层顶上：现场一长，切队友不该先滚回去。 */
.site-agents {
  position: sticky;
  top: 0;
  z-index: var(--z-raised-2);
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px 6px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.turn__who {
  font-weight: 600;
  color: var(--ink);
}
.site-agents__tab {
  padding: 2px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.site-agents__tab:hover {
  background: var(--fill);
}
.site-agents__tab--on {
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}
.site-older {
  padding: 2px 0 8px;
  text-align: center;
  font-size: 12px;
  color: var(--faint);
}
/* 这个 tab 的唯一滚动层。原来它是 .tool-content（抽屉的滚动容器），
   现在滚动条属于 tab 自己。 */
.panel-site {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  /* 纵向滚，横向不滚。只写 `overflow-y: auto` 的话，`overflow-x` 会被算成
     `auto`：一行没折开的旧记录就够把这一栏撑出一条横向滚动条，而它藏在面板
     底下，读的人既看不见也不知道要往右拉。横向溢出由各行自己收掉。 */
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--surface);
}

/* 施工现场 timeline: 芝士 speaks (avatar + text), tools render as Claude-Code
   action lines (圆点 + 动作, 缩进箭头 + 参数预览). */
.site-log {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
/* 组内密、组间疏：一轮里的步骤挨着，轮与轮之间隔开并划一条发丝线。全都等距
   就等于没有分组 —— 二十条等距的行读起来是一堵墙，不是一段经过。 */
.turn + .turn {
  margin-top: 20px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.turn__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--faint);
}
.turn__time {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* 「3 步 · 2 分钟」里有中文，不走等宽：中文没有等宽字形，会掉到别的字体上。数字
   靠 tabular-nums 对齐就够了。 */
.turn__meta {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.turn__live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ok-ink);
}
.turn__pulse {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--ok);
  animation: site-breathe 1.6s ease-in-out infinite;
}
/* 全局的减弱动效兜底会把时长压到 0.001ms，1.6s 的呼吸就变成频闪 —— 比不动更
   糟，所以这里自己关掉。关掉之后那个点还在，「进行中」三个字也还在。 */
@media (prefers-reduced-motion: reduce) {
  .turn__pulse {
    animation: none;
  }
}
@keyframes site-breathe {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.site-msg {
  display: flex;
  gap: 8px;
  margin: 6px 0 2px;
}
.site-msg__av {
  flex: 0 0 auto;
  margin-top: 1px;
}
.site-msg__main {
  flex: 1 1 auto;
  min-width: 0;
}
.site-msg__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.site-msg__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
/* 一步一行。重复的是动词，有信息的是参数，所以动词退成固定宽度的次要列，参数
   拿正文色 —— 扫下来看见的是文件名和命令在变，不是二十遍「执行命令」。 */
.site-act {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.55;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.site-act:hover {
  background: var(--fill);
}
.site-act:hover .site-act__time {
  opacity: 1;
}
/* 平台动作是这一轮的产出（交出一份成果、写文档、递验收卡），不该和 ls 长得
   一样——靠墨色和字重拉开，不靠琥珀：这一栏里没有主操作。 */
/* 挂了的一步：圆点换成危险色。动词和参数照旧 —— 这一行说的还是它做了什么，
   变的只是它有没有做成。 */
.site-act--failed .site-act__dot {
  background: var(--danger);
}
/* 错误摘要缩进到参数那一列，和上面那一行对齐。这个缩进是圆点 + 间隙 + 动词列
   + 间隙 —— 写成 calc 而不是量出来的一个数，改了上面这一行不用回来改它。
   一条启动失败的原文可能很长：折到三行，整段能说多少说多少，鼠标停一下看全文，
   点开这一行摊开那一份。截成一行的话，读的人只拿到开头那半句，而这一行恰恰是
   出问题时唯一有人真去读的字。 */
.site-act__error {
  display: -webkit-box;
  flex: 0 0 100%;
  order: 2;
  min-width: 0;
  margin: 2px 0 0;
  padding: 0 0 0 calc(5px + 8px + 4em + 8px);
  border: 0;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  overflow-wrap: anywhere;
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
  color: var(--danger-ink);
  /* 13px 而不是 12：这一行是挂了的那一步上唯一有人真去读的字。 */
  font-size: 13px;
  line-height: 1.5;
}
.site-act__error:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
.site-act__error--full {
  display: block;
  -webkit-line-clamp: none;
  line-clamp: none;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 圆点分级：淡 = 普通的读、搜、跑；实 = 平台动作（cheese 工具、写文档）。 */
.site-act__dot {
  flex: 0 0 auto;
  width: 5px;
  height: 5px;
  border-radius: var(--radius-pill);
  background: var(--faint);
  /* 圆点没有文字基线，按行高把它压到第一行的中线上。 */
  transform: translateY(-3px);
}
.site-act__dot--platform {
  background: var(--ink);
}
/* 4em = 四个汉字，绝大多数动词正好这么宽，参数因此对齐成一列。更长的那几个
   （平台动作）自己把这一行的参数推开，而它们本来就该显眼。 */
.site-act__verb {
  flex: 0 0 auto;
  min-width: 4em;
  font-family: var(--font-sans);
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.site-act--platform .site-act__verb {
  color: var(--ink);
  font-weight: 600;
}
/* 这一步只有动词、没有参数：平台提示和后端报错都是这样 —— 内容本来就是一句话，
   落库时没有「动词\n参数」那道换行（见 lib/siteLog.ts 的 eventArg）。定宽不折行
   的动词列装不下整句，而按内容宽量出来的尺寸比这一行还长，于是它先整段掉到圆点
   下面另起一行（圆点孤零零占一行），再横着冲出面板。这时动词就是这一行的正文：
   从 0 起算占满剩下的宽度，折到三行，整句挂在 title 上。 */
.site-act--solo .site-act__verb {
  display: -webkit-box;
  flex: 1 1 0;
  min-width: 0;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: normal;
}
/* 折起来的动词是个 `overflow: hidden` 的盒子，它的基线算在底边而不是第一行，
   圆点和时间会跟着掉到三行的底下去。这里把它们钉回第一行顶上：时间是同字号的
   一行字，顶对顶就和动词第一行对齐；圆点不跟基线走了，基类那 -3px 的上移也要
   撤掉，改按行高居中：(18.6 - 5) / 2 ≈ 7，18.6 = 12px 的字 × 1.55 的行高。 */
.site-act--solo .site-act__dot,
.site-act--solo .site-act__time {
  align-self: flex-start;
}
.site-act--solo .site-act__dot {
  margin-top: 7px;
  transform: none;
}
/* 同一行里那个空的参数位不再和动词分宽度。 */
.site-act--solo .site-act__argtext {
  display: none;
}
/* 截断而不是折行：一条几百字符的命令折下来能占掉半屏，而这一列的用处是扫。
   点开这一行换成参数原文，整条摊开，不再截第二次。
   是个 button 而不是带 click 的 span：摊开是一个真的操作，键盘要够得着它。 */
/* 起始宽度是 0，不是内容宽：这一行是可折行的 flex，按内容宽起算的话，一条长参数
   量出来比剩下的空间宽，就整段掉到动词下面一行去，而不是在这一行里截断。 */
.site-act__argtext {
  flex: 1 1 0;
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.site-act__argtext:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
/* 摊开时参数换到动词下面另起一行，动词和时间留在第一行——整行被参数原文顶掉的
   话，读的人就看不见「这一步是什么、什么时候做的」了。缩进和错误行同一个算法：
   圆点 + 间隙 + 动词列 + 间隙。order 把它排到最后，time 用 margin-left 顶到最右。 */
.site-act__argtext--full {
  flex: 0 0 100%;
  order: 1;
  padding-left: calc(5px + 8px + 4em + 8px);
  white-space: pre-wrap;
  word-break: break-word;
}
.site-act__argtext--full ~ .site-act__time {
  margin-left: auto;
  /* 摊开时时间不再等悬停：参数原文拉到下面去了，这一行只剩动词和它，是「什么时候
     做的」唯一的落点。 */
  opacity: 1;
}
.site-act--platform .site-act__argtext {
  color: var(--ink);
}
.site-act__argtext--prose {
  font-family: var(--font-sans);
}
/* 时间悬停才出现：二十个同样的 20:28 占着最右边的强位置，却不说明任何事，这一
   轮的时间写在组头上。位置照留，不然一行会在鼠标划过时改变宽度。 */
.site-act__time {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: 12px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
/* 芝士说话的那一段：排版规则（标题、列表、代码块、表格）来自全局的 .md-content，
   这里只定这一栏自己的字号 —— 现场比对话栏密，13px 和旁边那些工具行对得上。 */
.site-msg__body {
  font-size: 13px;
  /* 行高按 token 取 13px 那一档：折叠的 max-height 正好是它的 12 倍，两处都写死
     数字的话，改一处另一处就跟着错位。 */
  line-height: var(--lh-13);
  word-break: break-word;
  color: var(--text);
}
/* 底部渐隐要盖在正文上面，所以它在正文外面那一层：放进正文里会被正文自己的
   overflow: hidden 一起剪掉。 */
.site-msg__clip {
  position: relative;
}
.site-msg__clip--clamped::after {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 28px;
  pointer-events: none;
  background: linear-gradient(to bottom, transparent, var(--surface));
  content: '';
}
/* Collapsed long entry: an explicit max-height, NOT -webkit-line-clamp. The
   clamp version is ignored the moment the markdown holds a block-level <pre>,
   so a 128-line code block rendered at its full height (measured 2615px) while
   the button still said 展开全部. max-height clips the real height, block
   children included. The line count comes from SITE_CLAMP_LINES via a bound
   custom property rather than a literal here: the template asks that same
   module whether to render the 展开 button, so if the two drift an entry gets
   clamped with no way out of the clamp. */
.site-msg__body--clamped {
  max-height: calc(var(--site-clamp-lines) * var(--lh-13));
  overflow: hidden;
}
.site-msg__more {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--faint);
  cursor: pointer;
}
.site-msg__more:hover {
  color: var(--text);
  text-decoration: underline;
}
</style>
