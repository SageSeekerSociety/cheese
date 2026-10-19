<script setup lang="ts">
// 现场 tab: 芝士 干活的实况 —— 会话的控制条，加上重建出来的 transcript 时间线。
//
// 这一只只画，也只管编排：顶上那条会话栏、按队友筛的那一排、谁在干活，然后一轮一组
// 地铺开。一行长什么样（一步、还是说话）和组头长什么样，各在自己那一只里
// （`SiteActRow` / `SiteSayRow` / `SiteTurnHead`）。
//
// 这一窗怎么来（读最近一页、往上翻、socket 上来的行落到哪儿）、顶上那条会话栏的
// 轮询、摊开一步之后去取哪一段输出，都在 `composables/usePanelSite.ts` 里由
// `components/work/PanelSiteHost.vue` 调一次，整包从 `site` 递进来。和别处同一个理由：
// 场景棘轮认的「场景」是 `components/panels/**` 下每个 SFC，A 档的意思是「给一组
// props 就能单独出画面」，取数一滴都不能漏进来。
import type { PanelSiteBundle } from '../../composables/usePanelSite'
import type { AgentControlState, Block } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { computed, onUpdated, ref, useId } from 'vue'

import { isAgentHandle } from '../../lib/authorship'
import { groupByTurn, isLongSiteEntry, isNarration } from '../../lib/siteLog'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MemberActivity from '../room/MemberActivity.vue'
import SessionInspector from '../SessionInspector.vue'

import SiteActRow from './SiteActRow.vue'
import SiteAgentTabs from './SiteAgentTabs.vue'
import SiteSayRow from './SiteSayRow.vue'
import SiteTurnHead from './SiteTurnHead.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

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
// someone just opened is the same bug as scrolling them away from it. 哪一行都
// 读它 —— 一步、一句说话、还有它们底下那条输出。
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

function baseLabel(handle: string): string {
  return props.memberNames[handle] || (isAgentHandle(handle) ? props.agentName : handle)
}
// 两个页签叫同一个名字（名册上没有的 AI 都落成项目队友的名字）时，从第二个起带上序号，
// 不然点哪个都分不清。
const agentLabels = computed(() => {
  const seen = new Map<string, number>()
  const labels: Record<string, string> = {}
  for (const handle of agents.value) {
    const base = baseLabel(handle)
    const n = (seen.get(base) ?? 0) + 1
    seen.set(base, n)
    labels[handle] = n === 1 ? base : t('work.room.site.agents.numbered', { name: base, n })
  }
  return labels
})
function agentLabel(handle: string): string {
  return agentLabels.value[handle] ?? baseLabel(handle)
}

// 组头上署谁。「全部」下几个队友的轮次交错着排，组头不写是谁的，交错的两段就分不开。
// 只看一个队友、或者房间里只有一个时，名字是多余的。
function turnWho(entries: Block[]): string | null {
  if (viewing.value !== null || agents.value.length < 2) return null
  const who = entries.find((b) => agents.value.includes(b.author))
  return who ? agentLabel(who.author) : null
}

// 此刻在干活的队友（对话栏从 socket 上学来，和输入框下面那一行是同一份）：只看
// 一个队友时只说它。
const workingLines = computed(() =>
  props.activity.filter((l) => l.kind === 'working' && (viewing.value === null || l.handle === viewing.value))
)

// 芝士自己说的话也走 kind=event —— 没有显式发布的输出就是这么落库的（后端
// `_persist_assistant_message` 的 as_progress 分支）。它不是一步操作，所以既不
// 按工具行渲染，也不计进步数。旧数据没有 meta，按原样走工具行那条路。
function isSay(b: Block): boolean {
  return b.kind !== 'event' || isNarration(b.meta)
}

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
      <SiteAgentTabs
        :agents="agents"
        :labels="agentLabels"
        :viewing="viewing"
        :controls="logId"
        @select="selectAgent"
      />
      <MemberActivity :lines="workingLines" class="site-activity" />
      <div v-if="transcript.length === 0" :id="logId" :role="agents.length > 1 ? 'tabpanel' : undefined">
        <BaseEmptyState :title="t('work.room.site.empty')" />
      </div>
      <div v-else :id="logId" class="site-log pa-3" :role="agents.length > 1 ? 'tabpanel' : undefined">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? t('work.room.site.loadingOlder') : t('work.room.site.older') }}
        </div>
        <section v-for="(turn, index) in turns" :key="turn.key" class="turn" :class="{ 'turn--loose': turn.loose }">
          <SiteTurnHead
            v-if="!turn.loose"
            :turn="turn"
            :who="turnWho(turn.entries)"
            :live="isLive(index)"
            :starts-at-ms="turnStarts[turn.key]"
          />
          <template v-for="b in turn.entries" :key="b.id">
            <SiteActRow
              v-if="!isSay(b)"
              :block="b"
              :expanded="expandedSite.has(b.id)"
              :member-names="memberNames"
              :topic-id="topicId"
              :load="loadStepOutput"
              @toggle="toggleSiteEntry"
            />
            <SiteSayRow
              v-else
              :block="b"
              :agent-name="agentName"
              :member-names="memberNames"
              :long="isLong(b)"
              :expanded="expandedSite.has(b.id)"
              @toggle="toggleSiteEntry"
              @mention-click="emit('mention-click', $event)"
              @open-topic="emit('open-topic', $event)"
              @open-file="emit('open-file', $event)"
            />
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
/* 不属于哪一轮的那一条（环境空闲释放了、某人改了文档）只是一行：没有组头，上下也不
   像一轮那样隔开。 */
.turn--loose + .turn,
.turn + .turn--loose {
  margin-top: 8px;
  padding-top: 8px;
}
</style>
