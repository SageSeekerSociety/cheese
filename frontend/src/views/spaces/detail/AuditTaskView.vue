<template>
  <PageHeader :title="t('spaces.detail.auditTasks.title')" show-on-mobile />
  <div class="audit">
    <!-- A failed read leaves `tasks` empty, so the list would say "nothing to review" — the same shape as a queue that really is empty. Replace it instead. -->
    <BaseLoadError
      v-if="failed"
      :title="t('spaces.detail.auditTasks.loadFailed')"
      :error="errorReason"
      :forbidden="forbidden"
      @retry="emit('retry')"
    />
    <infinite-scroll
      v-else
      :loading="loadingMore"
      :has-more="hasMore"
      :initial-loading="refreshing"
      :is-empty="tasks.length === 0"
      :shown="tasks.length"
      :total="total"
      @load-more="emit('loadMore')"
    >
      <template #empty>
        <BaseEmptyState size="inline" class="audit__empty" :title="t('spaces.detail.auditTasks.noTasks')" />
      </template>
      <AuditTaskRow
        v-for="task in tasks"
        :key="task.id"
        :task="task"
        :open="expandedTaskId === task.id"
        @toggle="toggleExpand(task.id)"
        @approve="emit('approve', task.id)"
        @reject="emit('reject', task.id)"
      />
    </infinite-scroll>
  </div>
</template>

<script setup lang="ts">
// 待审核题目这一页的画面：列表、展开、通过/驳回。读数和写回都归页面 `AuditTask.vue`
// （场景规则见 docs/manual/dev/scenes.md）。展开哪一行是这一页自己的画面状态，
// 留在画面里；通过/驳回只把题目 id 发出去。
import type { Task } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AuditTaskRow from './AuditTaskRow.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import PageHeader from '@/components/common/PageHeader.vue'

defineProps<{
  tasks: Task[]
  failed: boolean
  errorReason: string | null
  forbidden: boolean
  loadingMore: boolean
  hasMore: boolean
  refreshing: boolean
  total: number
}>()

const emit = defineEmits<{
  retry: []
  loadMore: []
  approve: [taskId: number]
  reject: [taskId: number]
}>()

const { t } = useI18n()

const expandedTaskId = ref<number | null>(null)

const toggleExpand = (taskId: number) => {
  expandedTaskId.value = expandedTaskId.value === taskId ? null : taskId
}
</script>

<style scoped>
.audit {
  /* 宽屏下封顶居中（和项目里的页面一样），不再左贴、右边空一条。 */
  max-width: 960px;
  margin-inline: auto;
  padding: 8px 16px 48px;
}

.audit__empty {
  padding: 32px 8px;
}
</style>
