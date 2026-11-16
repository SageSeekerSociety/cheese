<script setup lang="ts">
// 项目文件的改动记录：整个项目的，或一份文件的。每次改动一行——来自哪件任务（平台采纳
// 合并的才有）、谁、什么时候；来自任务的那几行能进那件任务。
//
// **只认 props**：取数在 `views/library/ProjectFilesView.vue`。
import type { ProjectChange } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  open: boolean
  title: string
  changes: ProjectChange[]
  loading: boolean
  /** 没取到时为什么；取到了是 null。 */
  failed: string | null
}>()

const emit = defineEmits<{
  close: []
  retry: []
  'open-task': [taskId: string]
}>()
</script>

<template>
  <AdaptiveDialog
    :model-value="props.open"
    :title="props.title"
    :cancel-label="t('work.library.versionsClose')"
    size="md"
    @update:model-value="(open: boolean) => !open && emit('close')"
  >
    <BaseLoadError
      v-if="props.failed"
      :title="t('work.library.projectFiles.historyFailed')"
      :error="props.failed"
      @retry="emit('retry')"
    />
    <p v-else-if="props.loading" class="changes__loading t-meta">
      {{ t('work.library.projectFiles.historyLoading') }}
    </p>
    <BaseEmptyState
      v-else-if="!props.changes.length"
      size="inline"
      :title="t('work.library.projectFiles.historyEmpty')"
    />
    <ol v-else class="changes__list" :aria-label="props.title">
      <li v-for="change in props.changes" :key="change.sha" class="changes__row">
        <div class="changes__main">
          <span class="changes__what t-body" data-user-content>{{
            change.task ? t('work.library.projectFiles.taskTitle', { title: change.task.title }) : change.subject
          }}</span>
          <span class="changes__meta t-meta c-muted">
            <span data-user-content>{{ change.author }}</span>
            <span>{{ relTime(change.at) }}</span>
          </span>
        </div>
        <BaseButton v-if="change.task" size="sm" @click="emit('open-task', change.task.id)">
          {{ t('work.library.projectFiles.openTask') }}
        </BaseButton>
      </li>
    </ol>
  </AdaptiveDialog>
</template>

<style scoped>
.changes__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.changes__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}

.changes__row:last-child {
  border-bottom: 0;
}

.changes__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.changes__what {
  overflow-wrap: anywhere;
  color: var(--ink);
}

.changes__meta {
  display: flex;
  gap: 12px;
}

.changes__loading {
  margin: 0;
  color: var(--muted);
}
</style>
