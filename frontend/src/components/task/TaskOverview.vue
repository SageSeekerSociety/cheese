<script setup lang="ts">
// 任务的概览：一整列，自然往下滚。从上到下是谁什么时候开始的（「与开始时相比」看从
// 那以后改了什么）、关闭时写下的结论、实况文档（顶栏下面是 AI 队友这一轮的清单，只在
// 它干活时有）、产出、相关。
//
// 从讨论转出来的任务，文档还空着时先是 AI 队友在整理它：那时这里说它整理到哪了，
// 失败了给「重试」，也可以自己写。
import type { ProjectMemberRow, RoomTask, TodoItem, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'
import type { TaskRelated } from '@/types/taskOrigin'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import TodoChecklist from '@/components/panels/TodoChecklist.vue'
import TaskDocCompare from '@/components/task/TaskDocCompare.vue'
import TaskOutputs from '@/components/task/TaskOutputs.vue'
import TaskRelatedList from '@/components/task/TaskRelatedList.vue'
import PanelDocHost from '@/components/work/PanelDocHost.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  room: Topic
  task: RoomTask
  memberNames: Record<string, string>
  activityTick: number
  topicList: Topic[]
  agentName: string
  agentHandle: string | null
  members: ProjectMemberRow[]
  comparing: boolean
  comparison: { before: string; after: string } | null
  compareError: string | null
  /** AI 队友这一轮的清单；没在干活时是空的。 */
  checklist: TodoItem[]
  related: TaskRelated | null
  /** 能不能让 AI 队友再整理一次：做这件任务的人才能。 */
  canRetry: boolean
  retrying: boolean
  retryError: string | null
}>()

const emit = defineEmits<{
  (e: 'toggle-compare'): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'retry-opening'): void
  (e: 'open-output', path: string): void
  (e: 'open-discussion', conversationId: string): void
  (e: 'open-document', id: string, title: string): void
  (e: 'open-file', path: string): void
}>()

// 第一轮失败后选了「自己写」：不再拦在文档前面。
const writingMyself = ref(false)
watch(
  () => props.task.id,
  () => (writingMyself.value = false)
)
const opening = computed(() => (writingMyself.value ? null : props.task.opening ?? null))
const done = computed(() => props.checklist.filter((i) => i.status === 'completed').length)

const docRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  reviewEdits: (request: DocReviewRequest) => void
} | null>(null)
defineExpose({
  pulse: () => docRef.value?.pulse(),
  highlightTurn: (turnId: string) => docRef.value?.highlightTurn(turnId),
  reviewEdits: (request: DocReviewRequest) => docRef.value?.reviewEdits(request),
})
</script>

<template>
  <div class="task-overview">
    <div v-if="task.started_at" class="task-overview__started t-meta">
      <span>{{
        t('work.task.startedLine', {
          who: memberNames[task.started_by ?? ''] || task.started_by || '',
          when: relTime(task.started_at),
        })
      }}</span>
      <template v-if="task.document_id && task.started_doc_version != null">
        <span aria-hidden="true">·</span>
        <button type="button" class="task-overview__compare" data-testid="task-compare" @click="emit('toggle-compare')">
          {{ comparing ? t('work.task.compareBack') : t('work.task.compare') }}
        </button>
      </template>
    </div>
    <div v-if="task.conclusion" class="task-overview__conclusion">
      <div class="t-meta c-faint">{{ t('work.task.conclusion') }}</div>
      <p class="t-body task-overview__conclusion-text">{{ task.conclusion }}</p>
    </div>

    <section v-if="opening" class="task-overview__opening" role="status" data-testid="task-opening">
      <template v-if="opening === 'failed'">
        <p class="task-overview__opening-head t-body">{{ t('work.task.opening.failed', { name: agentName }) }}</p>
        <p class="t-meta c-muted">{{ retryError ?? t('work.task.opening.failedWhy') }}</p>
        <div class="task-overview__opening-actions">
          <BaseButton v-if="canRetry" kind="primary" size="sm" :loading="retrying" @click="emit('retry-opening')">
            {{ t('work.task.opening.retry') }}
          </BaseButton>
          <BaseButton kind="secondary" size="sm" @click="writingMyself = true">
            {{ t('work.task.opening.writeMyself') }}
          </BaseButton>
        </div>
      </template>
      <template v-else>
        <p class="task-overview__opening-head t-body">{{ t('work.task.opening.drafting', { name: agentName }) }}</p>
        <p class="t-meta" :class="opening === 'waiting' ? 'task-overview__waiting' : 'c-muted'">
          {{ opening === 'waiting' ? t('work.task.opening.waiting') : t('work.task.opening.draftingWhat') }}
        </p>
      </template>
    </section>

    <template v-else-if="comparing">
      <p v-if="compareError" class="t-meta task-overview__error" role="alert">{{ compareError }}</p>
      <TaskDocCompare v-else-if="comparison" :before="comparison.before" :after="comparison.after" />
      <div v-else class="task-overview__state">
        <v-progress-circular indeterminate color="primary" size="20" />
      </div>
    </template>
    <PanelDocHost
      v-else
      ref="docRef"
      flow
      :topic="room"
      :task-id="task.id"
      :activity-tick="activityTick"
      :topic-list="topicList"
      :agent-name="agentName"
      :agent-handle="agentHandle"
      :members="members"
      @open-topic="emit('open-topic', $event)"
      @mention-click="emit('mention-click', $event)"
      @open-file="emit('open-file', $event)"
    >
      <template #lead>
        <section v-if="checklist.length" class="task-overview__checklist" data-testid="task-checklist">
          <div class="task-overview__checklist-head t-meta">
            <span>{{ t('work.task.checklist', { name: agentName }) }}</span>
            <span class="task-overview__tally">{{ done }}/{{ checklist.length }}</span>
          </div>
          <TodoChecklist :items="checklist" />
        </section>
      </template>
    </PanelDocHost>

    <TaskOutputs :topic-id="task.id" @open="emit('open-output', $event)" />
    <TaskRelatedList
      :related="related"
      :member-names="memberNames"
      @open-discussion="emit('open-discussion', $event)"
      @open-document="(id, title) => emit('open-document', id, title)"
      @open-file="emit('open-file', $event)"
    />
  </div>
</template>

<style scoped>
.task-overview {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}
.task-overview__started {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
}
.task-overview__compare {
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  cursor: pointer;
}
.task-overview__compare:hover {
  color: var(--ink);
  text-decoration: underline;
}
.task-overview__conclusion {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}
.task-overview__conclusion-text {
  margin: 4px 0 0;
  color: var(--ink);
  white-space: pre-wrap;
}
.task-overview__opening {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 24px;
  text-align: center;
}
.task-overview__opening p {
  margin: 0;
}
.task-overview__opening-head {
  font-weight: 600;
  color: var(--ink);
}
.task-overview__waiting {
  color: var(--warn-ink);
}
.task-overview__opening-actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.task-overview__checklist {
  margin: 12px 16px 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
}
.task-overview__checklist-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
  color: var(--muted);
}
.task-overview__tally {
  font-variant-numeric: tabular-nums;
}
.task-overview__error {
  padding: 16px;
  color: var(--danger-ink);
}
.task-overview__state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
</style>
