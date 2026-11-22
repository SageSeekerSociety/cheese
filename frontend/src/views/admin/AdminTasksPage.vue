<template>
  <AdminTasksPageView
    :tasks="tasks"
    :failed="failed"
    :error-reason="errorReason"
    :forbidden="forbidden"
    :loading-more="loadingMore"
    :has-more="hasMore"
    :refreshing="refreshing"
    :total="total"
    @retry="refresh"
    @load-more="loadMore"
    @approve="approveTask"
    @reject="rejectTask"
  />
</template>

<script setup lang="ts">
// 管理后台的「题目审核」（`/admin/tasks`）：平台管理员审所有空间里等审核的题，不必是
// 那个空间的管理员。翻页、通过/驳回、驳回原因对话框与空间里的 `AuditTask.vue` 一样；
// 画面在 `AdminTasksPageView.vue`。
import type { Task } from '@/types'

import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

import { usePaging } from '@/utils/paging'

import AdminTasksPageView from './AdminTasksPageView.vue'

import { axiosErrorMessage } from '@/api/axiosHttp'
import { TaskBoardApi } from '@/api/taskBoard'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { toast } from '@/lib/toast'
import { CancelError, useDialog } from '@/plugins/dialog'

defineOptions({ name: 'AdminTasksPage' })

const { t } = useI18n()
const dialogs = useDialog()

const {
  data: tasks,
  error,
  refresh,
  loadMore,
  hasMore,
  refreshing,
  loadingMore,
  total,
} = usePaging<Task, void, string>(async (pageStart) => {
  const data = await TaskBoardApi.unreviewedEverywhere(pageStart)
  return { data: data.tasks, page: data.page }
})

// 读失败时 `tasks` 是空的，光看「一条都没有」分不出读失败；只有没东西可显示时才换成错误。
const failed = computed(() => error.value !== null && tasks.value.length === 0)
const errorReason = computed(() => loadFailureReason(error.value))
const forbidden = computed(() => isForbidden(error.value))

const approveTask = async (taskId: number) => {
  try {
    await TaskBoardApi.reviewAsPlatformAdmin(taskId, { approved: 'APPROVED' })
    toast.success(t('spaces.detail.auditTasks.operationSuccess'))
  } catch (error) {
    console.error(error)
    // 服务端说了为什么不行（如「先审核更早的题」）就照说，没说才用那句泛泛的。
    toast.error(axiosErrorMessage(error, t('spaces.detail.auditTasks.operationFailed')))
  } finally {
    await refresh()
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
    await TaskBoardApi.reviewAsPlatformAdmin(taskId, { approved: 'DISAPPROVED', rejectReason: reason })
    toast.success(t('spaces.detail.auditTasks.operationSuccess'))
  } catch (error) {
    if (error instanceof CancelError) {
      return
    }
    console.error(error)
    toast.error(axiosErrorMessage(error, t('spaces.detail.auditTasks.operationFailed')))
  } finally {
    await refresh()
  }
}

onMounted(refresh)
</script>
