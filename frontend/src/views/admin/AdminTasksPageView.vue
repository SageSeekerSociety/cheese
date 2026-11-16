<script setup lang="ts">
// 管理后台的「题目审核」（`/admin/tasks`）的画法：所有空间里等审核的题，一行一道，
// 和空间里的「待审核题目」是同一种行（`AuditTaskRow`），只多写一句它在哪个空间。
// 取数与写都在页面里（`AdminTasksPage.vue`），这里只吃数据、往上报动作。
import type { Task } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import AuditTaskRow from '@/views/spaces/detail/AuditTaskRow.vue'

defineOptions({ name: 'AdminTasksPageView' })

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

<template>
  <AdminPage :title="t('navigation.admin.tasks')" :sub="t('spaces.detail.auditTasks.adminHelp')">
    <div class="admin-page__body">
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
          <BaseEmptyState size="inline" :title="t('spaces.detail.auditTasks.noTasks')" />
        </template>
        <AuditTaskRow
          v-for="task in tasks"
          :key="task.id"
          :task="task"
          :open="expandedTaskId === task.id"
          show-space
          @toggle="toggleExpand(task.id)"
          @approve="emit('approve', task.id)"
          @reject="emit('reject', task.id)"
        />
      </infinite-scroll>
    </div>
  </AdminPage>
</template>
