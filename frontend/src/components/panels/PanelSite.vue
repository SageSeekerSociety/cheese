<script setup lang="ts">
import type { PanelSiteBundle } from '../../composables/usePanelSite'
import type { AgentControlState, Block } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { computed, nextTick, useId } from 'vue'

import { isAgentHandle } from '../../lib/authorship'
import { overviewRecords, recordParts, recordTree } from '../../lib/sessionRecords'
import { groupByTurn } from '../../lib/siteLog'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MemberActivity from '../room/MemberActivity.vue'
import SessionInspector from '../SessionInspector.vue'

import SiteAgentTabs from './SiteAgentTabs.vue'
import SiteRecordTree from './SiteRecordTree.vue'
import SiteTurnHead from './SiteTurnHead.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
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
    runningTurns: () => ({}),
    refreshTick: 0,
    agentControl: null,
    agentName: () => t('work.room.defaultAgentName'),
    activity: () => [],
    onlyTurn: null,
  }
)

const logId = `site-log-${useId()}`
const emit = defineEmits<{
  (e: 'open-file', path: string): void
  (e: 'open-topic', id: string): void
  (e: 'mention-click', handle: string): void
}>()
const {
  turnStarts,
  scrollRef,
  agents,
  viewing,
  transcript,
  hasOlder,
  loading,
  loadingOlder,
  errorMsg,
  errorForbidden,
  arrived,
  retry,
  onSiteScroll,
  selectAgent,
  inspector,
  loadImage,
  viewMode,
  selectView,
} = props.site
const visible = computed(() =>
  viewing.value === null ? transcript.value : transcript.value.filter((b) => b.author === viewing.value)
)
/** 这条记录该不该出现在概览的分组里。
 *
 * 平台内部的记账行（`command_lifecycle` 那类「输入排队了」）没有可显示的内容，
 * 原始记录视图里才把它们摊成 JSON。它们没有轮次 id，`groupByTurn` 会给每个这样的
 * 块单独成一组，于是在概览里顶出一串「时刻 · 0 步」、底下什么都没有的组头。
 *
 * 只丢这一类：**不属于任何一轮、又画不出行**的独立块。属于某一轮的记录一律留着——
 * 它撑着那一组的组头和「进行中」，哪怕这一轮此刻还没有画得出来的步骤（每轮开头那条
 * `command_lifecycle/started` 就是这种）；画不画那一行由 `SiteRecordRow` 自己决定。 */
function keepsGroup(block: Block): boolean {
  if (viewMode.value === 'raw') return true
  if (block.turn_id) return true
  if (!block.meta?.record) return true
  return recordParts(block).some((part) => part.kind !== 'thinking')
}
const turns = computed(() => {
  const all = groupByTurn(visible.value.filter(keepsGroup), turnStarts.value)
  return props.onlyTurn ? all.filter((turn) => turn.key === props.onlyTurn) : all
})
function baseLabel(handle: string): string {
  return props.memberNames[handle] || (isAgentHandle(handle) ? props.agentName : handle)
}
const agentLabels = computed(() => {
  const seen = new Map<string, number>()
  return Object.fromEntries(
    agents.value.map((handle) => {
      const base = baseLabel(handle)
      const n = (seen.get(base) ?? 0) + 1
      seen.set(base, n)
      return [handle, n === 1 ? base : t('work.room.site.agents.numbered', { name: base, n })]
    })
  )
})
function turnWho(entries: Block[]): string | null {
  if (viewing.value !== null || agents.value.length < 2) return null
  const who = entries.find((b) => agents.value.includes(b.author))
  return who ? agentLabels.value[who.author] ?? baseLabel(who.author) : null
}
const workingLines = computed(() =>
  props.activity.filter((l) => l.kind === 'working' && (viewing.value === null || l.handle === viewing.value))
)
/** 表头钉在滚动层顶上，所以「此刻看得见的第一行」要从表头下沿算起，不是滚动容器
 *  自己的上沿；否则换视图时会把一行藏在表头底下的记录当成锚点。 */
function viewportTop(el: HTMLElement): number {
  const head = el.querySelector<HTMLElement>('.site-head')
  return (head ?? el).getBoundingClientRect().bottom
}
async function switchView(mode: 'overview' | 'raw') {
  const el = scrollRef.value
  const top = el ? viewportTop(el) : 0
  const anchor = Array.from(el?.querySelectorAll<HTMLElement>('[data-record-id]') ?? []).find(
    (row) => row.getBoundingClientRect().bottom > top
  )
  const id = anchor?.dataset.recordId
  const offset = anchor ? anchor.getBoundingClientRect().top - top : 0
  selectView(mode)
  await nextTick()
  if (!el || !id) return
  const target = el.querySelector<HTMLElement>(`[data-record-id="${id}"]`)
  if (target) el.scrollTop += target.getBoundingClientRect().top - viewportTop(el) - offset
}
</script>

<template>
  <div ref="scrollRef" class="panel-site" @scroll="onSiteScroll">
    <!-- 这一栏是日志的「表头」，不跟着记录滚：打开现场停在最新一条，读者正看着底下，
         会话状态、概览／原始记录、队友页签如果在上头，就等于要翻回最顶才够得着。
         钉住的只有这几行控件；记录本身照旧在下面滚。 -->
    <div class="site-head">
      <SessionInspector
        v-if="topicId"
        :session="inspector"
        :project-id="projectId"
        @mention-click="emit('mention-click', $event)"
      >
        <template #actions>
          <div class="site-view" role="group" :aria-label="t('work.room.site.view')">
            <button type="button" :aria-pressed="viewMode === 'overview'" @click="switchView('overview')">
              {{ t('work.room.site.overview') }}
            </button>
            <button type="button" :aria-pressed="viewMode === 'raw'" @click="switchView('raw')">
              {{ t('work.room.site.raw') }}
            </button>
          </div>
        </template>
      </SessionInspector>
      <SiteAgentTabs
        :agents="agents"
        :labels="agentLabels"
        :viewing="viewing"
        :controls="logId"
        @select="selectAgent"
      />
      <MemberActivity :lines="workingLines" class="site-activity" />
    </div>
    <LoadingSkeleton v-if="loading" :variant="viewMode === 'raw' ? 'siteRaw' : 'site'" :rows="5" />
    <!-- 读失败留在它读的那块地方，并摆一条能走的路（docs/design-system.md §3.10）：
         从前的红条没有按钮，超时一次就只能刷新整页才回得来。 -->
    <BaseLoadError
      v-else-if="errorMsg"
      class="ma-4"
      :title="t('work.room.site.loadFailed')"
      :error="errorMsg"
      :forbidden="errorForbidden"
      @retry="retry"
    />
    <template v-else>
      <div v-if="transcript.length === 0" :id="logId" :role="agents.length > 1 ? 'tabpanel' : undefined">
        <BaseEmptyState :title="t('work.room.site.empty')" />
      </div>
      <div v-else :id="logId" class="site-log pa-3" :role="agents.length > 1 ? 'tabpanel' : undefined">
        <div v-if="hasOlder" class="site-older">
          {{ loadingOlder ? t('work.room.site.loadingOlder') : t('work.room.site.older') }}
        </div>
        <section
          v-for="turn in turns"
          :key="turn.key"
          class="turn"
          :data-turn-id="turn.key"
          :class="{ 'turn--loose': turn.loose }"
        >
          <SiteTurnHead
            v-if="!turn.loose"
            :turn="turn"
            :who="turnWho(turn.entries)"
            :live="working && turn.key in (runningTurns ?? {})"
            :starts-at-ms="turnStarts[turn.key]"
          />
          <SiteRecordTree
            :nodes="recordTree(viewMode === 'raw' ? turn.entries : overviewRecords(turn.entries))"
            :raw="viewMode === 'raw'"
            :load-image="loadImage"
            :member-names="memberNames"
            :arrived="arrived"
            @settle="arrived.delete($event)"
          />
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.panel-site {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--surface);
}
/* 现场的表头：会话栏、概览／原始记录、队友页签、谁在忙。打开这一格停在最新一条时，
   读者正看着底下，这几行不能跟着记录一起走。只有它是钉住的；日志仍在下面滚。 */
.site-head {
  position: sticky;
  top: 0;
  z-index: var(--z-raised-2);
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
/* 分隔线由表头自己画一条：会话栏那条和它贴在一起会变成两像素。 */
.site-head .session-inspector {
  border-bottom: 0;
}
.site-view {
  display: flex;
  gap: 2px;
}
.site-view button {
  min-height: 28px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
}
.site-view button[aria-pressed='true'] {
  color: var(--ink);
  background: var(--fill);
}
.site-view button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
/* 活动条也在表头里，不再自己钉一次（两个 sticky 同顶会互相盖住）。 */
.site-activity {
  padding-block: 6px;
  border-bottom: 0;
  background: var(--surface);
}
.site-older {
  padding: 2px 0 8px;
  text-align: center;
  font-size: 13px;
  color: var(--muted);
}
.site-log {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.turn + .turn {
  margin-top: 20px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.turn--loose + .turn,
.turn + .turn--loose {
  margin-top: 8px;
  padding-top: 8px;
}
@media (pointer: coarse) {
  .site-view button {
    min-height: 44px;
  }
}
</style>
