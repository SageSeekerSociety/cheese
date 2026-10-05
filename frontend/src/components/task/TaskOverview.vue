<script setup lang="ts">
// 任务的总览：它的实况文档，上面一行说谁什么时候开始的、点「与开始时相比」看从那以后
// 改了什么；关闭时写下的结论排在文档前面。
import type { ProjectMemberRow, RoomTask, Topic } from '@/cx_types'

import PanelDoc from '@/components/panels/PanelDoc.vue'
import TaskDocCompare from '@/components/task/TaskDocCompare.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

defineProps<{
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
}>()

const emit = defineEmits<{
  (e: 'toggle-compare'): void
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
}>()
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
    <template v-if="comparing">
      <p v-if="compareError" class="t-meta task-overview__error" role="alert">{{ compareError }}</p>
      <TaskDocCompare v-else-if="comparison" :before="comparison.before" :after="comparison.after" />
      <div v-else class="task-overview__state">
        <v-progress-circular indeterminate color="primary" size="20" />
      </div>
    </template>
    <PanelDoc
      v-else
      class="task-overview__doc"
      :topic="room"
      :task-id="task.id"
      :activity-tick="activityTick"
      :topic-list="topicList"
      :agent-name="agentName"
      :agent-handle="agentHandle"
      :members="members"
      @open-topic="emit('open-topic', $event)"
      @mention-click="emit('mention-click', $event)"
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
.task-overview__doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
