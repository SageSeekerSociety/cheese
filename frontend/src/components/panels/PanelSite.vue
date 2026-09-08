<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 优先接真实终端（跑这一轮的机器上的
// screen 通道），接不上就渲染重建出来的 transcript 时间线。
//
// 这个 tab 的输入是隔离的：worklog / working / workingSince 只有它用，别的 tab
// 一概不需要，所以它们直接从 WorkPanel 透传到这里，不进任何共享状态。
import type { Block, Topic } from '../../cx_types'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { getTerminal, getTranscript, SITE_PAGE_SIZE } from '../../api'
import { countLines, isLongSiteEntry, shouldFollowTail, shouldKeepPinning, SITE_CLAMP_LINES } from '../../lib/siteLog'
import { isPlatformEvent, summarizeActions, toolLabel } from '../../lib/toolLabels'
import CheeseAvatar from '../CheeseAvatar.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import DeviceLiveViewer from '../DeviceLiveViewer.vue'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // 施工现场: this topic's AI tool-action log for the turn in flight.
    // platform: amber dot (cheese action) vs neutral dot (plain work).
    worklog?: { label: string; text: string; platform?: boolean }[]
    // A turn is in flight — the live feed's newest line pulses.
    working?: boolean
    // Epoch ms when the current turn's first tool ran (drives the ⏱ elapsed).
    workingSince?: number | null
    // This tab is the one on screen. Load happens on the rising edge, exactly
    // like opening the old drawer did.
    active?: boolean
    // 这个房间现在交给的那个 AI 队友叫什么。一个项目可以有好几个队友，房间随时
    // 能换，所以这里不能写死「芝士」——这一栏和对话栏说的是同一个人。
    agentName?: string
  }>(),
  { worklog: () => [], working: false, workingSince: null, active: false, agentName: '芝士' }
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

// 本轮实时动作 appends to the bottom of the same list while a turn runs, so it
// has to follow the tail too — otherwise 现场 opens on the newest entry and then
// grows out of view while you watch it. Only when the reader is already parked
// at the bottom: someone who scrolled up to read a tool argument is reading it.
watch(
  () => props.worklog.length,
  () => {
    const el = scrollRef.value
    if (props.active && el && shouldFollowTail(el)) scrollSiteToTail()
  }
)

// 现场实时终端: when a machine has this topic's screen open, 现场 embeds the real
// pane instead of the rebuilt worklog. The probe hands back the screen
// WebSocket path, and 现场 embeds DeviceLiveViewer on it.
const screenSid = ref<string | null>(null)

// Live-turn elapsed seconds (ticks while `working`).
const nowTick = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
watch(
  () => props.working,
  (w) => {
    if (tickTimer) clearInterval(tickTimer)
    tickTimer = w ? setInterval(() => (nowTick.value = Date.now()), 1000) : null
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  if (tickTimer) clearInterval(tickTimer)
})
const liveElapsed = computed(() => {
  if (!props.working || !props.workingSince) return null
  return Math.max(0, Math.round((nowTick.value - props.workingSince) / 1000))
})
const liveSummary = computed(() => summarizeActions(props.worklog.map((w) => w.label)))

async function load() {
  const tid = props.topic?.id
  if (!tid) return
  loading.value = true
  errorMsg.value = null
  try {
    // Prefer the real pane on the machine running the turn; fall back to the
    // worklog timeline. The terminal probe must never break 现场 — on any error
    // it just stays null and the worklog view renders.
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
  return b.author_type === 'ai' ? props.agentName : b.author
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
  if (b.meta?.tool) return toolLabel(b.meta.tool)
  const first = (b.content.split('\n')[0] || '').replace(/^🔧\s*/, '')
  return LEGACY_VERB[first] ?? first
}
function eventArg(b: Block): string {
  if (b.meta?.tool) return b.meta.arg ?? ''
  const nl = b.content.indexOf('\n')
  return nl >= 0 ? b.content.slice(nl + 1).trim() : ''
}
// 圆点分级: amber = platform action, neutral = plain work (structured fields
// only — never guessed from the content text).
function eventPlatform(b: Block): boolean {
  return isPlatformEvent(b.meta, b.refs)
}
</script>

<template>
  <div ref="scrollRef" class="panel-site" @scroll="onSiteScroll">
    <LoadingSkeleton v-if="loading" variant="entry" :rows="5" class="pa-3" />
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">
      {{ errorMsg }}
    </v-alert>

    <!-- 设备上的话题: the machine screen's REAL terminal, byte-for-byte over the
         screen WebSocket, and INTERACTIVE — typing here reaches the pane (the
         backend gates input by the same authorization as watching). -->
    <div v-else-if="screenSid" class="term-wrap">
      <div class="term-bar text-caption px-3 py-1">
        <v-icon class="term-bar__dot" size="10">mdi-circle</v-icon>
        实时终端 · 机器上的 Claude Code，可直接输入
      </div>
      <DeviceLiveViewer :sid="screenSid" />
    </div>

    <!-- read-only transcript timeline (芝士 messages + tool events) -->
    <template v-else>
      <div v-if="transcript.length === 0 && worklog.length === 0" class="text-center text-medium-emphasis py-6">
        暂无现场记录
      </div>
      <div v-else class="site-log pa-3">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? '加载更早的现场…' : '更早的现场' }}
        </div>
        <template v-for="b in transcript" :key="b.id">
          <!-- Tool action — Claude Code style: 圆点 + 动作 + 参数预览 -->
          <div v-if="b.kind === 'event'" class="site-act">
            <v-icon class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(b) }" size="8"
              >mdi-circle</v-icon
            >
            <div class="site-act__body">
              <span class="site-act__verb">{{ eventVerb(b) }}</span>
              <div v-if="eventArg(b)" class="site-act__arg">
                <v-icon class="site-act__argicon" size="12">mdi-subdirectory-arrow-right</v-icon>
                <span class="site-act__argtext" data-testid="site-act-arg">{{ eventArg(b) }}</span>
              </div>
            </div>
            <span class="site-act__time">{{ fmtTime(b.created_at) }}</span>
          </div>
          <!-- 芝士 speaks — shown as a person, with avatar (like the chat) -->
          <div v-else class="site-msg">
            <!-- 头像上的字取的是这个房间当前那个队友的名字，和它右边写的名字同一个来源。 -->
            <CheeseAvatar :size="26" :name="agentName" class="site-msg__av" />
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

        <!-- 本轮实时动作 (live feed): what 芝士 is doing RIGHT NOW — newest line
             pulses; the list clears when the turn ends and the persisted
             transcript above becomes the record. -->
        <template v-for="(act, i) in worklog" :key="'live-' + i">
          <div class="site-act">
            <v-icon
              class="site-act__dot"
              :class="{
                'site-act__dot--platform': act.platform,
                'site-act__dot--live': working && i === worklog.length - 1,
              }"
              size="8"
              >mdi-circle</v-icon
            >
            <div class="site-act__body">
              <span class="site-act__verb">{{ act.text }}</span>
            </div>
          </div>
        </template>
        <!-- 本轮聚合摘要 (Claude Code 风): deterministic counts + 耗时 -->
        <div v-if="working && worklog.length" class="site-summary">
          <v-icon class="site-act__dot site-act__dot--live" size="8">mdi-circle</v-icon>
          <span>
            {{ liveSummary }}
            <template v-if="liveElapsed !== null"> （{{ liveElapsed }}s） </template>
          </span>
        </div>
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
  gap: 14px;
}
.site-msg {
  display: flex;
  gap: 8px;
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
.site-act {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.5;
}
.site-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--line-2);
  font-size: 12px;
  color: var(--muted);
}
@keyframes site-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
@media (prefers-reduced-motion: reduce) {
  .site-act__dot--live {
    animation: none;
  }
}
/* 圆点分级: neutral = plain work (read/search/run), amber = platform action
   (cheese tool / cheese CLI / doc edit). --live (pulse) overrides both. */
/* 图标盒子没有文字基线，行改成顶对齐后要手动把 8px 圆点压到第一行的中线上
   ((12.5px × 1.5 − 8px) / 2 ≈ 5px)。摘要行是 align-items: center，不用补。 */
.site-act__dot {
  flex: 0 0 auto;
  margin-top: 5px;
  color: var(--faint);
}
.site-summary .site-act__dot {
  margin-top: 0;
}
.site-act__argicon {
  flex: 0 0 auto;
  margin-top: 3px;
  color: var(--faint);
}
.site-act__dot--platform {
  color: var(--accent);
}
/* Declared last so the live pulse wins over both dot tiers. */
.site-act__dot--live {
  color: rgb(var(--v-theme-primary));
  animation: site-pulse 1.2s ease-in-out infinite;
}
.site-act__body {
  flex: 1 1 auto;
  min-width: 0;
}
.site-act__verb {
  color: var(--text);
}
/* 图标和文字分成两个 flex 子项（而不是把图标塞进 pre-wrap 的文本流里）：
   pre-wrap 会把模板里的换行和缩进照样画出来，而 flex 布局顺带给了折行时的
   悬挂缩进 —— 第二行对齐到箭头右边，正是那个箭头本来的意思。 */
.site-act__arg {
  display: flex;
  gap: 3px;
  margin-top: 1px;
  color: var(--faint);
}
.site-act__argtext {
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.site-act__time {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: 11px;
  font-family: var(--font-mono);
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
