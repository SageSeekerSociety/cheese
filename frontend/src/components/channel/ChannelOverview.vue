<script setup lang="ts">
// 频道的概览：一整列，自然往下滚。从上到下是频道说明（综合是项目总览）、置顶、
// 进行中的任务（各带最后说的一句）、最近完成。频道没有自己的实况文档：要一起看的
// 东西钉在置顶里，要做成的事是任务。
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { ProjectMemberRow, RoomTask, Topic } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ChannelOverviewPins from '@/components/channel/ChannelOverviewPins.vue'
import PanelDocHost from '@/components/work/PanelDocHost.vue'
import { t } from '@/i18n'
import { replySnippet } from '@/lib/blockDisplay'
import { columnDotStyle, phraseLabel } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  topic: Topic
  /** 综合：最上面是项目总览，不是频道说明。 */
  general: boolean
  /** 项目总览是哪一份文档；只有综合有。 */
  overview: PanelDocument | null
  pins: ChannelPin[]
  tasks: RoomTask[]
  /** 能不能取消置顶：在主线说得上话的人。 */
  canPin: boolean
  memberNames: Record<string, string>
  agentName: string
  agentHandle: string | null
  members: ProjectMemberRow[]
  topicList: Topic[]
  activityTick: number
  saveDescription: (text: string) => Promise<boolean>
}>()

const emit = defineEmits<{
  (e: 'unpin', blockId: string): void
  (e: 'jump', blockId: string): void
  (e: 'open-task', taskId: string): void
  (e: 'open-all'): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()

/** 最近完成列几件：其余在看板里。 */
const RECENT_DONE = 3

const refs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} }))
const nameOf = (handle: string) => props.memberNames[handle] || handle

const running = computed(() =>
  props.tasks.filter((task) => task.status === 'open' && task.presentation.column !== 'done')
)
const recentDone = computed(() =>
  props.tasks
    .filter((task) => task.status !== 'open' || task.presentation.column === 'done')
    .sort((a, b) => (b.closed_at ?? '').localeCompare(a.closed_at ?? ''))
    .slice(0, RECENT_DONE)
)

function latest(task: RoomTask): string | null {
  const block = task.last_message
  if (!block) return null
  return t('work.channel.overview.latest', { name: nameOf(block.author), text: replySnippet(block, refs.value, 80) })
}

// ---- 频道说明：管理者就地改 ----
const editing = ref(false)
const draft = ref('')
const saving = ref(false)
watch(
  () => props.topic.id,
  () => (editing.value = false)
)
function edit() {
  draft.value = props.topic.description ?? ''
  editing.value = true
}
async function save() {
  saving.value = true
  try {
    if (await props.saveDescription(draft.value.trim())) editing.value = false
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="channel-overview" data-testid="channel-overview">
    <section v-if="general" class="co-section co-section--overview">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.project') }}</h3>
      </div>
      <p class="t-meta c-faint co-note">{{ t('work.channel.overview.projectNote', { name: agentName }) }}</p>
      <PanelDocHost
        v-if="overview"
        bare
        flow
        class="co-doc"
        :topic="topic"
        :document="overview"
        :activity-tick="activityTick"
        :topic-list="topicList"
        :agent-name="agentName"
        :agent-handle="agentHandle"
        :members="members"
        @open-topic="emit('open-topic', $event)"
        @mention-click="emit('mention-click', $event)"
      />
    </section>
    <section v-else class="co-section">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.description') }}</h3>
        <button v-if="topic.can_manage && !editing" type="button" class="co-link t-meta" @click="edit">
          {{ t('work.channel.overview.edit') }}
        </button>
      </div>
      <div v-if="editing" class="co-edit">
        <v-textarea
          v-model="draft"
          auto-grow
          rows="2"
          density="compact"
          hide-details
          maxlength="500"
          autocomplete="off"
          :aria-label="t('work.channel.overview.description')"
        />
        <div class="co-edit__actions">
          <BaseButton kind="primary" size="sm" :loading="saving" @click="save">
            {{ t('work.channel.overview.save') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="editing = false">{{
            t('work.channel.overview.cancel')
          }}</BaseButton>
        </div>
      </div>
      <p v-else-if="topic.description" class="t-body co-description">{{ topic.description }}</p>
      <p v-else class="t-meta c-faint">{{ t('work.channel.overview.noDescription') }}</p>
    </section>

    <ChannelOverviewPins
      :pins="pins"
      :can-pin="canPin"
      :member-names="memberNames"
      :refs="refs"
      @unpin="emit('unpin', $event)"
      @jump="emit('jump', $event)"
    />

    <section class="co-section">
      <h3 class="co-title">{{ t('work.channel.overview.running', { count: running.length }) }}</h3>
      <p v-if="!running.length" class="t-meta c-faint">{{ t('work.channel.overview.noRunning') }}</p>
      <button
        v-for="task in running"
        :key="task.id"
        type="button"
        class="co-task"
        data-testid="channel-task"
        @click="emit('open-task', task.id)"
      >
        <span class="co-task__head">
          <span class="co-task__title t-body">{{ taskTitle(task) }}</span>
          <span class="co-task__state t-meta">
            <span class="co-dot" :style="columnDotStyle(task.presentation.column)" aria-hidden="true" />
            {{ phraseLabel(task.presentation.phrase) }}
          </span>
        </span>
        <span v-if="latest(task)" class="co-task__latest t-meta">{{ latest(task) }}</span>
        <span class="co-task__meta t-meta c-faint">{{
          t('work.channel.overview.owner', {
            name: nameOf(task.owner_handle ?? ''),
            when: relTime(task.last_message?.created_at ?? task.created_at),
          })
        }}</span>
      </button>
    </section>

    <section v-if="recentDone.length" class="co-section">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.recentDone') }}</h3>
        <button type="button" class="co-link t-meta" @click="emit('open-all')">
          {{ t('work.channel.overview.allTasks') }}
        </button>
      </div>
      <button
        v-for="task in recentDone"
        :key="task.id"
        type="button"
        class="co-done"
        @click="emit('open-task', task.id)"
      >
        <v-icon size="14" class="co-done__mark">mdi-check</v-icon>
        <span class="co-done__title t-body">{{ taskTitle(task) }}</span>
        <span class="t-meta c-faint">{{ relTime(task.closed_at ?? task.created_at) }}</span>
      </button>
    </section>
  </div>
</template>

<style scoped>
.channel-overview {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 24px;
  min-width: 0;
  min-height: 0;
  padding: 16px 16px 32px;
  overflow-y: auto;
}
.co-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.co-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.co-title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.co-note {
  margin: 0;
}
.co-doc {
  margin: 0 -16px;
}
.co-link {
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.co-link:hover {
  color: var(--ink);
}
.co-description {
  margin: 0;
  color: var(--text);
  white-space: pre-wrap;
}
.co-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.co-edit__actions {
  display: flex;
  gap: 8px;
}
.co-task {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0 -8px;
  padding: 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.co-task:hover,
.co-done:hover {
  background: var(--fill);
}
.co-task__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.co-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--ink);
}
.co-task__state {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}
.co-dot {
  width: 8px;
  height: 8px;
  border: 1.5px solid var(--faint);
  border-radius: var(--radius-pill);
}
.co-task__latest {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  color: var(--text);
}
.co-done {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 -8px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.co-done__mark {
  color: var(--ok);
}
.co-done__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}
</style>
