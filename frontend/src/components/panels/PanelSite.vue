<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 会话的控制条，加上重建出来的 transcript 时间线。
//
// 这一只只画。这一窗怎么来（读最近一页、往上翻、socket 上来的行落到哪儿）、顶上那条
// 会话栏的轮询、摊开一步之后去取哪一段输出，都在 `composables/usePanelSite.ts` 里由
// `components/work/PanelSiteHost.vue` 调一次，整包从 `site` 递进来。和别处同一个理由：
// 场景棘轮认的「场景」是 `components/panels/**` 下每个 SFC，A 档的意思是「给一组
// props 就能单独出画面」，取数一滴都不能漏进来。
import type { PanelSiteBundle } from '../../composables/usePanelSite'
import type { AgentControlState, Block } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { computed, onUpdated, ref, useId } from 'vue'

import { isAgentBlock, isAgentHandle } from '../../lib/authorship'
import {
  argDisplay,
  countLines,
  eventArg,
  eventFailed,
  eventVerb,
  formatSpan,
  groupByTurn,
  isLongSiteEntry,
  isNarration,
  isProseArg,
  isRunRecord,
  SITE_CLAMP_LINES,
} from '../../lib/siteLog'
import { isPlatformEvent } from '../../lib/toolLabels'
import CheeseAvatar from '../CheeseAvatar.vue'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MarkdownView from '../common/MarkdownView.vue'
import MemberActivity from '../room/MemberActivity.vue'
import SessionInspector from '../SessionInspector.vue'

import SiteStepOutput from './SiteStepOutput.vue'

import { t } from '@/i18n'
import { vRovingTabs } from '@/lib/rovingTabs'

const props = withDefaults(
  defineProps<{
    // 这段对话的 id：房间的，或者任务的。
    topicId: string | null
    /** 会话栏里「由 X 授权」那颗 chip 去哪：项目 ID 换来项目里的成员页。 */
    projectId?: string | null
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
    /** 这一格的取数（`composables/usePanelSite.ts` 那一包）。 */
    site: PanelSiteBundle
    /** 只看这一轮（支线里「查看过程」）；null 是整段对话。 */
    onlyTurn?: string | null
  }>(),
  {
    projectId: null,
    active: false,
    memberNames: () => ({}),
    working: false,
    agentControl: null,
    agentName: () => t('work.room.defaultAgentName'),
    activity: () => [],
    onlyTurn: null,
  }
)

// 按队友筛的那排页签切换的就是下面这条记录。
const logId = `site-log-${useId()}`

const emit = defineEmits<{
  (e: 'open-file', path: string): void
  (e: 'open-topic', id: string): void
  (e: 'mention-click', handle: string): void
}>()

// 摊开而不是留着那一包：这一格的接口就是这十来样东西，谁传谁看得见。摊开之后模板里
// 那些名字还是老样子（`transcript`、`agents`、`loading`……），因为它们现在都是顶层的 ref。
const {
  turnStarts,
  scrollRef,
  overflowing,
  measured,
  measureClamp,
  agents,
  viewing,
  transcript,
  hasOlder,
  loading,
  loadingOlder,
  errorMsg,
  onSiteScroll,
  selectAgent,
  inspector,
  loadStepOutput,
} = props.site

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

// A fresh read of "which entries are long enough to clamp" follows every render,
// coalesced to one per frame. The measurement itself — together with the
// ResizeObserver that re-reads it when the panel's own width changes — is in
// useSiteClamp; 读的时机在这一只，因为「渲染完了」只有它知道。
onUpdated(measureClamp)

// Long enough to be clamped: the measured answer once we have it, the content
// heuristic until then. Both answer the same question, so the 展开 button and
// the clamp never disagree about which entries are long.
function isLong(b: Block): boolean {
  return measured.value ? overflowing.value.has(b.id) : isLongSiteEntry(b.content)
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
// 省略同住 lib/siteLog.ts（isProseArg / argDisplay），模板直接用那两个。

// 摊开这一行之后显示的那一份：参数原文，一个字都没剪。没有第二份时摊开的仍是
// 这一行本身 —— 面板窄到把它省略掉时，展开是唯一能看全的办法。
function eventDetail(b: Block): string {
  return b.meta?.detail || eventArg(b)
}
function eventError(b: Block): string {
  return b.meta?.error ?? ''
}
// 平台写的一句里点到的人是 `<@handle>`：照对话栏换成名字，认不出的留 handle。
function named(text: string): string {
  return text.replace(/<@([^>\s]+)>/g, (_, handle: string) => props.memberNames?.[handle] ?? handle)
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

// 芝士说的话按 markdown 渲染，和对话栏走同一条路（common/MarkdownView）。
// 这一栏原来是把原文摆出来（mono + pre-wrap，Claude Code 会话那种），但一段汇报
// 落到人眼里就是一堆星号和反引号，粗体、列表、代码块全丢了信息。引用 token 也照
// 对话栏展开成 chip —— 光看 `<@handle>` `<&path>` 是认不出人的。
const sayRefs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} }))

// chip 是 v-html 塞进来的，点击只能从容器上委派（同对话栏）。文件在哪一份里找，由
// 面板按它此刻读的那段对话决定。
function onSayClick(event: MouseEvent): void {
  const chip = (event.target as HTMLElement | null)?.closest('.mention') as HTMLElement | null
  if (!chip) return
  if (chip.dataset.handle) emit('mention-click', chip.dataset.handle)
  else if (chip.dataset.topic) emit('open-topic', chip.dataset.topic)
  else if (chip.dataset.file) emit('open-file', chip.dataset.file)
}

// 在跑的那一轮的起始时间由取数那一层从对话栏记下（`usePanelSite`），这里只管分组。
const turns = computed(() => {
  const all = groupByTurn(visible.value, turnStarts.value)
  return props.onlyTurn ? all.filter((turn) => turn.key === props.onlyTurn) : all
})

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
      <SessionInspector
        v-if="topicId"
        :session="inspector"
        :project-id="projectId"
        @mention-click="emit('mention-click', $event)"
      />
      <div
        v-if="agents.length > 1"
        v-roving-tabs
        class="site-agents"
        role="tablist"
        :aria-label="t('work.room.site.agents.label')"
      >
        <button
          type="button"
          role="tab"
          class="site-agents__tab"
          :class="{ 'site-agents__tab--on': viewing === null }"
          :aria-selected="viewing === null"
          :aria-controls="logId"
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
          :aria-controls="logId"
          @click="selectAgent(a)"
        >
          {{ agentLabel(a) }}
        </button>
      </div>
      <MemberActivity :lines="workingLines" class="site-activity" />
      <div
        v-if="transcript.length === 0"
        :id="logId"
        class="text-center text-medium-emphasis py-6"
        :role="agents.length > 1 ? 'tabpanel' : undefined"
      >
        {{ t('work.room.site.empty') }}
      </div>
      <div v-else :id="logId" class="site-log pa-3" :role="agents.length > 1 ? 'tabpanel' : undefined">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? t('work.room.site.loadingOlder') : t('work.room.site.older') }}
        </div>
        <section v-for="(turn, index) in turns" :key="turn.key" class="turn" :class="{ 'turn--loose': turn.loose }">
          <!-- 组头：这一轮从什么时候开始、几步、多久。触发这一轮的那句话在对话
               栏，现场读不到它（人写的块不带 turn_id），所以这里不写标题。 -->
          <div v-if="!turn.loose" class="turn__head">
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
                'site-act--record': isRunRecord(b),
                'site-act--warn': isRunRecord(b) && b.meta?.severity === 'warn',
              }"
            >
              <i class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(b) }" />
              <span class="site-act__verb" :title="named(soloVerb(b)) || undefined">{{ named(eventVerb(b)) }}</span>
              <button
                v-if="eventArg(b)"
                type="button"
                class="site-act__argtext"
                :class="{
                  'site-act__argtext--full': expandedSite.has(b.id),
                  'site-act__argtext--prose': isProseArg(b),
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
                v-if="topicId && expandedSite.has(b.id) && b.meta?.output_bytes"
                :block-id="b.id"
                :bytes="b.meta.output_bytes"
                :load="loadStepOutput"
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
                   和对话栏一个样子（走同一个 MarkdownView）。 -->
                <!-- Clamp = max-height + a fade, NOT -webkit-line-clamp: that one
                    is ignored the moment the markdown holds a block-level <pre>,
                    so a 128-line code block rendered at full height and the
                    展开 button opened nothing. The fade sits on the wrapper,
                    because the body's own overflow:hidden would clip it. -->
                <div
                  class="site-msg__clip"
                  :class="{ 'site-msg__clip--clamped': isLong(b) && !expandedSite.has(b.id) }"
                >
                  <MarkdownView
                    class="site-msg__body md-content"
                    :class="{ 'site-msg__body--clamped': isLong(b) && !expandedSite.has(b.id) }"
                    :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
                    :data-site-body="b.id"
                    :source="b.content"
                    as="chat"
                    :names="sayRefs"
                    @click="onSayClick($event)"
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
/* 不属于哪一轮的那一条（环境休眠了、某人改了文档）只是一行：没有组头，上下也不
   像一轮那样隔开。 */
.turn--loose + .turn,
.turn + .turn--loose {
  margin-top: 8px;
  padding-top: 8px;
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
  /* 只隔开同一行里的几列。错误摘要折到第二行时，行与行之间不另加空隙：它紧贴着
     出错的那一步。 */
  column-gap: 8px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.55;
  transition: background-color var(--dur-quick) var(--ease-standard);
  /* 离屏的一步不渲染，但留在 DOM 里（Ctrl+F、读屏还找得到）。22px 是「一步一行」的
     估计高度，只在这一行从未渲染过时用；`auto` 记住渲染过的真实高度。
     为什么不给 .site-msg 也加：useSiteClamp 每次更新都要读每一条 .site-msg__body 的
     scrollHeight（决定要不要夹），读离屏的就是强制把它铺开，加了也省不下来。
     测量帧的关掉见 lib/contentVisibility 与下面的 .cv-measure。 */
  content-visibility: auto;
  contain-intrinsic-size: auto 22px;
}
/* 测量帧（向上翻页补偿）：按真实高度铺开。见 lib/contentVisibility。 */
.cv-measure .site-act {
  content-visibility: visible;
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
/* 运行记录：平台运行中记下的事，不是芝士做的一步。同一套行，空心圆点、正文退一档；
   要留意的（重试、等机器）圆点和正文换成提醒色。 */
.site-act--record .site-act__dot {
  background: none;
  box-shadow: inset 0 0 0 1px var(--faint);
}
.site-act--record .site-act__argtext {
  font-family: var(--font-sans);
  color: var(--muted);
}
.site-act--warn .site-act__dot {
  box-shadow: inset 0 0 0 1px var(--warn);
}
.site-act--warn .site-act__argtext {
  color: var(--warn-ink);
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
