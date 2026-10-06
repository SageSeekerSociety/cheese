<template>
  <AuditTaskView
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
// 待审核题目这一页的容器：按空间翻页读、通过/驳回、驳回原因那个对话框。画面在
// `AuditTaskView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { Task } from '@/types'

import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useSpaceData } from '@/composables/useSpaceData'

import AuditTaskView from './AuditTaskView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { TasksApi } from '@/network/api/tasks'
import { CancelError, useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId } = storeToRefs(spaceStore)

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

// 读失败时 `tasks` 是空的，光看 `is-empty` 分不出「读失败」和「一条都没有」。
// 只有在**没有东西可显示**时才替换列表：翻页失败时上面那些条目还在。
const failed = computed(() => error.value !== null && tasks.value.length === 0)
const errorReason = computed(() => loadFailureReason(error.value))
const forbidden = computed(() => isForbidden(error.value))

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
