<script setup lang="ts">
import type { PanelSiteBundle } from '../../composables/usePanelSite'
import type { AgentControlState, Block } from '../../cx_types'
import type { MemberActivityLine } from '../../lib/memberActivity'

import { computed, nextTick, useId } from 'vue'

import { isAgentHandle } from '../../lib/authorship'
import { overviewRecords, recordTree } from '../../lib/sessionRecords'
import { groupByTurn } from '../../lib/siteLog'
import LoadingSkeleton from '../common/LoadingSkeleton.vue'
import MemberActivity from '../room/MemberActivity.vue'
import SessionInspector from '../SessionInspector.vue'

import SiteAgentTabs from './SiteAgentTabs.vue'
import SiteRecordTree from './SiteRecordTree.vue'
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
const turns = computed(() => {
  const all = groupByTurn(visible.value, turnStarts.value)
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
async function switchView(mode: 'overview' | 'raw') {
  const el = scrollRef.value
  const top = el?.getBoundingClientRect().top ?? 0
  const anchor = Array.from(el?.querySelectorAll<HTMLElement>('[data-record-id]') ?? []).find(
    (row) => row.getBoundingClientRect().bottom > top
  )
  const id = anchor?.dataset.recordId
  const offset = anchor ? anchor.getBoundingClientRect().top - top : 0
  selectView(mode)
  await nextTick()
  if (!el || !id) return
  const target = el.querySelector<HTMLElement>(`[data-record-id="${id}"]`)
  if (target) el.scrollTop += target.getBoundingClientRect().top - top - offset
}
</script>

<template>
  <div ref="scrollRef" class="panel-site" @scroll="onSiteScroll">
    <LoadingSkeleton v-if="loading" variant="site" :rows="5" />
    <v-alert v-else-if="errorMsg" type="error" density="compact" class="ma-4">{{ errorMsg }}</v-alert>
    <template v-else>
      <SessionInspector
        v-if="topicId"
        :session="inspector"
        :project-id="projectId"
        @mention-click="emit('mention-click', $event)"
      />
      <div class="site-view" role="group" :aria-label="t('work.room.site.view')">
        <button type="button" :aria-pressed="viewMode === 'overview'" @click="switchView('overview')">
          {{ t('work.room.site.overview') }}
        </button>
        <button type="button" :aria-pressed="viewMode === 'raw'" @click="switchView('raw')">
          {{ t('work.room.site.raw') }}
        </button>
      </div>
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
.site-view {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
}
.site-view button {
  min-height: 36px;
  padding: 8px 12px;
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
