<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 优先接真实终端（跑这一轮的机器上的
// screen 通道），接不上就渲染重建出来的 transcript 时间线。
import type { Block, Topic } from '../../cx_types'

import { computed, nextTick, ref, watch } from 'vue'

import { getTerminal, getTranscript, SITE_PAGE_SIZE } from '../../api'
import {
  countLines,
  formatSpan,
  groupByTurn,
  isLongSiteEntry,
  isNarration,
  shouldKeepPinning,
  SITE_CLAMP_LINES,
} from '../../lib/siteLog'
import { isPlatformEvent, toolLabel } from '../../lib/toolLabels'
import AgentControls from '../AgentControls.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import DeviceLiveViewer from '../DeviceLiveViewer.vue'

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
  }>(),
  { active: false, memberNames: () => ({}), working: false }
)

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
    errorMsg.value = e instanceof Error ? e.message : '加载更早的现场失败'
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

// 现场实时终端: when a machine has this topic's screen open, 现场 embeds the real
// pane instead of the rebuilt timeline. The probe hands back the screen
// WebSocket path, and 现场 embeds DeviceLiveViewer on it.
const screenSid = ref<string | null>(null)

async function load() {
  const tid = props.topic?.id
  if (!tid) return
  loading.value = true
  errorMsg.value = null
  try {
    // Prefer the real pane on the machine running the turn; fall back to the
    // rebuilt timeline. The terminal probe must never break 现场 — on any error
    // it just stays null and the timeline renders.
    const [tx, term] = await Promise.all([
      getTranscript(tid, { limit: SITE_PAGE_SIZE }),
      getTerminal(tid).catch(() => null),
    ])
    if (props.topic?.id !== tid) return
    transcript.value = tx.data
    hasOlder.value = tx.has_more === true
    // Follow the tail on every open of a topic's 现场 — that is what "open on
    // the newest" means.
    scrollSiteToTail()
    // `available` is the backend's own probe (credential + an open screen), so
    // a false here means the timeline below is the honest thing to show. The
    // sid out of the ws path ("/connector/session/{sid}/screen") is all
    // DeviceLiveViewer needs — it builds the socket URL itself.
    screenSid.value = (term?.available && term.ws?.match(/\/session\/([^/]+)\/screen/)?.[1]) || null
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (props.topic?.id === tid) loading.value = false
  }
}

// Opening the tab loads it, exactly like opening the drawer used to.
watch(
  () => props.active,
  (on) => {
    if (on) void load()
  },
  { immediate: true }
)

// Topic switch: drop the previous topic's terminal so it can't flash in the new
// 现场, and its transcript with it.
watch(
  () => props.topic?.id,
  () => {
    transcript.value = []
    screenSid.value = null
    expandedSite.value = new Set()
    errorMsg.value = null
    if (props.active) void load()
  }
)

function authorLabel(b: Block): string {
  return props.memberNames[b.author] || b.author
}

function fmtTime(iso: string): string {
  // Local HH:mm, not raw UTC slice.
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// 施工现场 tool-event lines: backend stores "verb\npreview"; legacy rows are
// "🔧 toolname". Split into the action verb and an optional argument preview.
const LEGACY_VERB: Record<string, string> = {
  update_doc: '更新文档',
  remember: '记入记忆',
  notify: '发送通知',
  request_accept: '提交验收卡',
  pin_milestone: '添加里程碑',
  write_file: '写入文件',
  record_decision: '记录决策',
}
// Meta-first rendering: an event block with structured meta ({tool, arg}) is
// translated at DISPLAY time via the full toolLabels table — so a verb missing
// from the table at write time is never frozen untranslated. Rows without meta
// (pre-meta data) fall back to the baked content text.
function eventVerb(b: Block): string {
  // as_tool 优先：一次 Bash 调用如果后端认出它其实在读文件，就按「读取文件」显示。
  // tool 仍然如实记着真正跑的是哪个工具。
  if (b.meta?.tool) return toolLabel(b.meta.as_tool ?? b.meta.tool)
  const first = (b.content.split('\n')[0] || '').replace(/^🔧\s*/, '')
  return LEGACY_VERB[first] ?? first
}
function eventArg(b: Block): string {
  if (b.meta?.tool) return b.meta.arg ?? ''
  const nl = b.content.indexOf('\n')
  return nl >= 0 ? b.content.slice(nl + 1).trim() : ''
}
// 摊开这一行之后显示的那一份：参数原文，一个字都没剪。没有第二份时摊开的仍是
// 这一行本身 —— 面板窄到把它省略掉时，展开是唯一能看全的办法。
function eventDetail(b: Block): string {
  return b.meta?.detail || eventArg(b)
}
// 这一步挂了没有。后端只在挂了的时候写这两个字段，所以「没有」就是「没挂」。
function eventFailed(b: Block): boolean {
  return b.meta?.failed === true
}
function eventError(b: Block): string {
  return b.meta?.error ?? ''
}
// 圆点分级: amber = platform action, neutral = plain work (structured fields
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

const turns = computed(() => groupByTurn(transcript.value))

// 最后一组还在跑吗。现场没有逐轮的生命周期，只有「这个房间有没有活」，所以只
// 给最后一组打这个标 —— 再往前的组都已经结束了。
function isLive(index: number): boolean {
  return props.working && index === turns.value.length - 1
}
</script>

<template>
  <div ref="scrollRef" class="panel-site" @scroll="onSiteScroll">
    <LoadingSkeleton v-if="loading" variant="site" :rows="5" />
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">
      {{ errorMsg }}
    </v-alert>

    <!-- 设备上的话题: the machine screen's REAL terminal, byte-for-byte over the
         screen WebSocket, and INTERACTIVE — typing here reaches the pane (the
         backend gates input by the same authorization as watching). -->
    <div v-else-if="screenSid" class="term-wrap">
      <AgentControls v-if="topic" :topic-id="topic.id" :active="active" />
      <div class="term-bar text-caption px-3 py-1">
        <v-icon class="term-bar__dot" size="10">mdi-circle</v-icon>
        实时终端 · 机器上的 Claude Code，可直接输入
      </div>
      <DeviceLiveViewer :sid="screenSid" />
    </div>

    <!-- read-only transcript timeline (芝士 messages + tool events) -->
    <template v-else>
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
                :class="{ 'site-act__argtext--full': expandedSite.has(b.id) }"
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
                <!-- Raw transcript text on purpose (决定: 现场内容改为raw): 现场 shows
                   what 芝士 actually emitted — markdown syntax, <@handle> tokens
                   and all — like a Claude Code session, NOT the rendered chat
                   version. -->
                <div
                  class="site-msg__raw"
                  :class="{ 'site-msg__raw--clamped': isLongSiteEntry(b.content) && !expandedSite.has(b.id) }"
                  :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
                >
                  {{ b.content }}
                </div>
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
.turn__meta {
  margin-left: auto;
  font-family: var(--font-mono);
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
  transition: background-color 0.12s ease;
}
.site-act:hover {
  background: var(--fill);
}
.site-act:hover .site-act__time {
  opacity: 1;
}
/* 平台动作是这一轮的产出（交出一份成果、写文档、递验收卡），不该和 ls 长得
   一样。 */
.site-act--platform {
  background: var(--accent-wash);
}
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
/* 圆点分级: neutral = plain work (read/search/run), amber = platform action
   (cheese tool / cheese CLI / doc edit). */
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
  background: var(--accent);
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
  color: var(--accent-ink);
}
/* 截断而不是折行：一条几百字符的命令折下来能占掉半屏，而这一列的用处是扫。
   点开这一行换成参数原文，整条摊开，不再截第二次。
   是个 button 而不是带 click 的 span：摊开是一个真的操作，键盘要够得着它。 */
.site-act__argtext {
  flex: 1 1 auto;
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
  color: var(--accent-ink);
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
  transition: opacity 0.12s ease;
}
/* 现场 is a transcript, not a doc — 芝士's messages are shown RAW (markdown
   source, <@handle> tokens intact), Claude Code style: mono + pre-wrap. */
.site-msg__raw {
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
}
/* Collapsed long entry. The height comes from SITE_CLAMP_LINES via a bound
   custom property rather than a literal here: the template asks that same
   module whether to render the 展开 button, so if the two drift an entry gets
   clamped with no way out of the clamp. */
.site-msg__raw--clamped {
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
/* 实时终端: the embedded pane fills the tab height. */
.term-wrap {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.term-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}
.term-bar__dot {
  color: var(--ok);
}
.term-frame {
  flex: 1 1 auto;
  width: 100%;
  border: none;
  /* Theme-invariant on purpose: this is the backing behind the pane, whose
     terminal paints its own black ground in both themes. A token here would
     flash a light slab under a black terminal during load. */
  /* stylelint-disable-next-line color-no-hex -- see the reason above */
  background: #000;
}
</style>
