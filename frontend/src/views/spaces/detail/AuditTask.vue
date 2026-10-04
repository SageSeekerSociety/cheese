<template>
  <PageHeader :title="t('spaces.detail.auditTasks.title')" show-on-mobile />
  <div class="audit">
    <infinite-scroll
      :loading="loadingMore"
      :has-more="hasMore"
      :initial-loading="refreshing"
      :is-empty="tasks.length === 0"
      :shown="tasks.length"
      :total="total"
      @load-more="loadMore"
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
        @approve="approveTask(task.id)"
        @reject="rejectTask(task.id)"
      />
    </infinite-scroll>
  </div>
</template>

<script setup lang="ts">
import type { Task } from '@/types'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useSpaceData } from '@/composables/useSpaceData'

import AuditTaskRow from './AuditTaskRow.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { TasksApi } from '@/network/api/tasks'
import { CancelError, useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId } = storeToRefs(spaceStore)

const expandedTaskId = ref<number | null>(null)

const { t } = useI18n()
const dialogs = useDialog()

const {
  data: tasks,
  refresh,
  loadMore,
  hasMore,
  refreshing,
  loadingMore,
  total,
} = usePaging<Task, void, string>(async (pageStart) => {
  if (!currentSpaceId.value) {
    return createEmptyResult<Task, string>()
  }
  const { data } = await TasksApi.list({
    space: currentSpaceId.value,
    pageStart: pageStart,
    sort_by: 'createdAt',
    sort_order: 'asc',
    approved: 'NONE',
    queryTopics: true,
    querySpace: true,
    // 这一屏要显示「提交要求」，而列表默认不带那张表单 —— 不点名要，
    // 底下那块永远走「无提交要求」那一支。
    querySubmissionSchema: true,
  })
  return { data: data.tasks as Task[], page: data.page }
})

const toggleExpand = (taskId: number) => {
  expandedTaskId.value = expandedTaskId.value === taskId ? null : taskId
}

const approveTask = async (taskId: number) => {
  try {
    await TasksApi.update(taskId, { approved: 'APPROVED' })
    toast.success(t('spaces.detail.auditTasks.operationSuccess'))
  } catch (error) {
    console.error(error)
    toast.error(t('spaces.detail.auditTasks.operationFailed'))
  } finally {
    await refresh()
    spaceData.fetchPendingAuditCount()
  }
}

const rejectTask = async (taskId: number) => {
  try {
    const rejectReason = await dialogs
      .prompt(t('spaces.detail.auditTasks.rejectReason'), {
        title: t('spaces.detail.auditTasks.rejectReasonDialogTitle'),
        required: true,
      })
      .wait()
    // 只有空格也算没写：驳回原因是作者改题的唯一依据。
    const reason = rejectReason?.trim()
    if (!reason) {
      toast.error(t('spaces.detail.auditTasks.rejectReasonRequired'))
      return
    }
    await TasksApi.update(taskId, { approved: 'DISAPPROVED', rejectReason: reason })
    toast.success(t('spaces.detail.auditTasks.operationSuccess'))
  } catch (error) {
    if (error instanceof CancelError) {
      return
    }
    console.error(error)
    toast.error(t('spaces.detail.auditTasks.operationFailed'))
  } finally {
    await refresh()
    spaceData.fetchPendingAuditCount()
  }
}

// The space is known only once the space layout has loaded it; opened cold
// (the address typed in, a reload) this page mounts first. Load when it is
// known, and again if it changes.
watch(
  currentSpaceId,
  (id) => {
    if (id) refresh()
  },
  { immediate: true }
)
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
