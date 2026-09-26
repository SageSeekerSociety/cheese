<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 会话的控制条，加上重建出来的 transcript 时间线。
import type { AgentControlState, Block, Topic } from '../../cx_types'

import { computed, nextTick, ref, watch } from 'vue'

import { getTranscript, SITE_PAGE_SIZE } from '../../api'
import { isAgentBlock, isAgentHandle } from '../../lib/authorship'
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
  shouldKeepPinning,
  SITE_CLAMP_LINES,
} from '../../lib/siteLog'
import { isPlatformEvent } from '../../lib/toolLabels'
import AgentControls from '../AgentControls.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'

import SiteStatusBar from './SiteStatusBar.vue'
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
    // 房间 socket 上最近一帧会话控制状态（对话栏收到，经 TopicView 转过来）。
    agentControl?: AgentControlState | null
    // 在跑的轮次 id → 开始时间（毫秒），对话栏从 socket 上算的。哪一组「进行中」、
    // 状态条上「已用多久」都读它。
    runningTurns?: Record<string, number>
    // 一轮结束时加一：趁这时把这一段安静地重读一遍，补上 socket 断开时漏掉的行。
    refreshTick?: number
    /** 名册里查不到名字的 AI 发言按这个名字称呼（项目 AI 队友的名字）。 */
    agentName?: string
  }>(),
  { active: false, memberNames: () => ({}), working: false, agentControl: null, agentName: '芝士' }
)

const emit = defineEmits<{
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', id: string): void
  (e: 'mention-click', handle: string): void
}>()

const loading = ref(false)
const errorMsg = ref<string | null>(null)

// 现场: read-only transcript timeline.
const transcript = ref<Block[]>([])
// 更早的现场还在库里没拉。和对话栏一样，只在读的人自己往上翻时才拉。
const hasOlder = ref(false)
const loadingOlder = ref(false)

async function loadOlder() {
  const tid = props.topic?.id
  const oldest = transcript.value[0]
  if (!tid || !oldest || loadingOlder.value || !hasOlder.value) return
  loadingOlder.value = true
  const el = scrollRef.value
  const before = el ? { top: el.scrollTop, height: el.scrollHeight } : null
  try {
    const page = await getTranscript(tid, { limit: SITE_PAGE_SIZE, before: oldest.id })
    if (props.topic?.id !== tid) return
    transcript.value = [...page.data, ...transcript.value]
    hasOlder.value = page.has_more === true
    // Prepending grows the content ABOVE the viewport; without this the reader
    // is thrown backwards by exactly that much (same fix as the chat's pager).
    await nextTick()
    const sc = scrollRef.value
    if (sc && before) sc.scrollTop = sc.scrollHeight - before.height + before.top
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loadingOlder.value = false
  }
}

function onSiteScroll() {
  const el = scrollRef.value
  if (el && el.scrollTop < 120) void loadOlder()
}
// The scroll container, so the timeline can open on its newest entry the way a
// chat log does. Measured before this existed: opening 现场 left scrollTop at 0
// with a scrollHeight of 1818 and a viewport of 500 — the reader landed 1300px
// above the thing they came to see.
const scrollRef = ref<HTMLElement | null>(null)
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
async function load() {
  const tid = props.topic?.id
  if (!tid) return
  const quiet = transcript.value.length > 0
  const follow = !quiet || atTail()
  if (!quiet) loading.value = true
  errorMsg.value = null
  try {
    const tx = await getTranscript(tid, { limit: SITE_PAGE_SIZE })
    if (props.topic?.id !== tid) return
    transcript.value = mergeSite(tx.data, transcript.value)
    if (!quiet) hasOlder.value = tx.has_more === true
    // Follow the tail on every open of a topic's 现场 — that is what "open on
    // the newest" means.
    if (follow) scrollSiteToTail()
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topic?.id === tid) loading.value = false
  }
}

// 一页读回来的，和这一栏手上已有的，按 id 合成一份。读的请求在路上的时候 socket
// 又送来了几行，它们比这一页新：留着，接在后面。手上那份里比这一页最老的一行还
// 老的（往上翻出来的更早的记录），也留着。
function mergeSite(page: Block[], held: Block[]): Block[] {
  if (!page.length) return held.length ? held : page
  const ids = new Set(page.map((b) => b.id))
  const first = Date.parse(page[0].created_at)
  const last = Date.parse(page[page.length - 1].created_at)
  const older = held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) < first)
  const newer = held.filter((b) => !ids.has(b.id) && Date.parse(b.created_at) >= last)
  return [...older, ...page, ...newer]
}

/**
 * socket 上来的一行（对话栏收到，一路转过来）：新的接在末尾，已有的原地换掉。
 * 别的话题的、分身卡上的、不是事件的，都不归这一栏。
 */
function receive(block: Block): void {
  if (block.topic_id !== props.topic?.id || block.kind !== 'event' || block.task_id) return
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

// Opening the tab loads it, exactly like opening the drawer used to.
watch(
  () => props.active,
  (on) => {
    if (on) void load()
  },
  { immediate: true }
)

// Topic switch: drop the previous topic's transcript so it can't flash in the new
// 现场.
watch(
  () => props.topic?.id,
  () => {
    transcript.value = []
    expandedSite.value = new Set()
    errorMsg.value = null
    if (props.active) void load()
  }
)

// ---- 按队友看 ----
// 一个房间可以先后、甚至同时交给几个队友。时间线是他们交错着的，而人来看的往往
// 是其中一个在干什么。作者就是做这一步的那个队友：做过一步、说过一句的参与者。
// 平台自己的话（署名 system）和人的动作只在「全部」里。
const agents = computed(() => {
  const seen: string[] = []
  for (const b of transcript.value) {
    if (b.author_type !== 'participant' || seen.includes(b.author)) continue
    if (b.meta?.tool || isNarration(b.meta)) seen.push(b.author)
  }
  return seen
})
// null = 全部。
const selectedAgent = ref<string | null>(null)
watch(
  () => props.topic?.id,
  () => (selectedAgent.value = null)
)
const viewing = computed(() =>
  selectedAgent.value !== null && agents.value.includes(selectedAgent.value) ? selectedAgent.value : null
)
const visible = computed(() =>
  viewing.value === null ? transcript.value : transcript.value.filter((b) => b.author === viewing.value)
)

function agentLabel(handle: string): string {
  return props.memberNames[handle] || (isAgentHandle(handle) ? props.agentName : handle)
}

// 在跑的轮次里，最近一行是谁做的：「全部」下状态条说的是这个队友。
const activeAgent = computed(() => {
  const running = props.runningTurns ?? {}
  let last: Block | undefined
  for (const b of transcript.value) {
    if (b.turn_id && b.turn_id in running && agents.value.includes(b.author)) last = b
  }
  return last?.author ?? null
})
// 选中的队友在不在干活：房间在干活，而且在跑的轮次里有它的行。在跑的轮次还一
// 行都没有时说不出是谁的，就不替任何一个说「没在干」。
const viewingWorking = computed(() => {
  if (!props.working || viewing.value === null) return props.working
  const running = props.runningTurns ?? {}
  const rows = transcript.value.filter((b) => b.turn_id && b.turn_id in running)
  return rows.length === 0 || rows.some((b) => b.author === viewing.value)
})
const statusAgent = computed(() => {
  if (agents.value.length < 2) return ''
  const who = viewing.value ?? activeAgent.value
  return who ? agentLabel(who) : ''
})

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

const turns = computed(() => groupByTurn(visible.value))

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
      <AgentControls v-if="topic" :topic-id="topic.id" :active="active" :pushed="agentControl" />
      <div v-if="agents.length > 1" class="site-agents" role="tablist">
        <button
          type="button"
          role="tab"
          class="site-agents__tab"
          :class="{ 'site-agents__tab--on': viewing === null }"
          :aria-selected="viewing === null"
          @click="selectedAgent = null"
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
          @click="selectedAgent = a"
        >
          {{ agentLabel(a) }}
        </button>
      </div>
      <SiteStatusBar :blocks="visible" :working="viewingWorking" :turns="runningTurns ?? {}" :agent="statusAgent" />
      <div v-if="transcript.length === 0" class="text-center text-medium-emphasis py-6">暂无现场记录</div>
      <div v-else class="site-log pa-3">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? '加载更早的现场…' : '更早的现场' }}
        </div>
        <section v-for="(turn, index) in turns" :key="turn.key" class="turn">
          <!-- 组头：这一轮从什么时候开始、几步、多久。触发这一轮的那句话在对话
               栏，现场读不到它（人写的块不带 turn_id），所以这里不写标题。 -->
          <div class="turn__head">
            <span class="turn__time">{{ fmtTime(turn.startedAt) }}</span>
            <span v-if="isLive(index)" class="turn__live">
              <i class="turn__pulse" />
              进行中
            </span>
            <span class="turn__meta">
              {{ turn.steps }} 步<template v-if="turn.seconds > 0"> · {{ formatSpan(turn.seconds) }}</template>
            </span>
          </div>
          <template v-for="b in turn.entries" :key="b.id">
            <!-- 一步一行：动词成列，参数占满剩下的宽度，时间悬停才出现。参数太
                 长时截断而不是折行 —— 点这一行摊开全文。 -->
            <div
              v-if="!isSay(b)"
              class="site-act"
              :class="{ 'site-act--platform': eventPlatform(b), 'site-act--failed': eventFailed(b) }"
            >
              <i class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(b) }" />
              <span class="site-act__verb">{{ eventVerb(b) }}</span>
              <button
                v-if="eventArg(b)"
                type="button"
                class="site-act__argtext"
                :class="{
                  'site-act__argtext--full': expandedSite.has(b.id),
                  'site-act__argtext--prose': argIsProse(b),
                }"
                data-testid="site-act-arg"
                :title="eventArg(b)"
                @click="toggleSiteEntry(b.id)"
              >
                {{ expandedSite.has(b.id) ? eventDetail(b) : eventArg(b) }}
              </button>
              <span v-else class="site-act__argtext"></span>
              <span class="site-act__time">{{ fmtTime(b.created_at) }}</span>
              <!-- 挂了的那一步：错误摘要另起一行，缩进到参数那一列，和上面对齐。 -->
              <p
                v-if="eventFailed(b) && eventError(b)"
                class="site-act__error"
                :class="{ 'site-act__error--full': expandedSite.has(b.id) }"
                data-testid="site-act-error"
              >
                {{ eventError(b) }}
              </p>
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
              <CheeseAvatar :size="26" :name="authorLabel(b)" class="site-msg__av" />
              <div class="site-msg__main">
                <div class="site-msg__meta">
                  <span class="site-msg__name">{{ authorLabel(b) }}</span>
                  <span class="t-meta">{{ fmtTime(b.created_at) }}</span>
                </div>
                <!-- 渲染成正文，不摆原文：现场读的也是人说的话，粗体、列表、代码块
                   和对话栏一个样子（走同一个 renderMarkdown）。 -->
                <div
                  class="site-msg__body md-content"
                  :class="{ 'site-msg__body--clamped': isLongSiteEntry(b.content) && !expandedSite.has(b.id) }"
                  :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
                  @click="onSayClick($event, b)"
                  v-html="renderSay(b.content)"
                />
                <!-- 过长时不直接摊开：一条几千字的输出会把它前后的所有东西挤出
                   屏幕，而 现场 的价值恰恰是「一眼看完发生了什么」。折叠到 12
                   行，想看全的自己点开。 -->
                <button
                  v-if="isLongSiteEntry(b.content)"
                  type="button"
                  class="site-msg__more"
                  @click="toggleSiteEntry(b.id)"
                >
                  {{ expandedSite.has(b.id) ? '收起' : `展开全部（${countLines(b.content)} 行）` }}
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
/* 按队友看：一排文字按钮，选中的那个换底色和墨色，不用琥珀——这里不是主操作。 */
.site-agents {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px 0;
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
   + 间隙 —— 写成 calc 而不是量出来的一个数，改了上面这一行不用回来改它。 */
.site-act__error {
  flex: 0 0 100%;
  margin: 2px 0 0;
  padding-left: calc(5px + 8px + 4em + 8px);
  color: var(--danger-ink);
  /* 13px 而不是 12：这一行是挂了的那一步上唯一有人真去读的字。 */
  font-size: 13px;
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.site-act__error--full {
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
.site-act__argtext--full {
  white-space: pre-wrap;
  word-break: break-word;
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
  line-height: 1.6;
  word-break: break-word;
  color: var(--text);
}
/* Collapsed long entry. The height comes from SITE_CLAMP_LINES via a bound
   custom property rather than a literal here: the template asks that same
   module whether to render the 展开 button, so if the two drift an entry gets
   clamped with no way out of the clamp. */
.site-msg__body--clamped {
  display: -webkit-box;
  -webkit-line-clamp: var(--site-clamp-lines);
  line-clamp: var(--site-clamp-lines);
  -webkit-box-orient: vertical;
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
