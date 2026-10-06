<template>
  <EditView
    ref="viewRef"
    :loading="loading"
    :error="error"
    :has-task-data="hasTaskData"
    :initial-data="editTaskData"
    :classification-topics="classificationTopics"
    :domain-groups="domainGroups"
    :is-resubmitting="isResubmitting"
    :show-resubmit-button="showResubmitButton"
    @retry="loadTaskData"
    @submit="handleSubmitEdit"
    @cancel="navigateToDetail"
    @resubmit="requestResubmit"
  />
</template>

<script setup lang="ts">
// 改题页的容器：读路由、取题、存题、跳转都在这儿；画面交给 EditView。
import type { TaskFormSubmitData } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { useTaskData, useTaskManagement } from './composables'
import EditView from './EditView.vue'

import { closeOverlay } from '@/lib/backOut'
import { TasksApi } from '@/network/api/tasks'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()

// Router
const router = useRouter()
const route = useRoute()
const taskId = Number(route.params.taskId)

// Composables
const taskDataModule = useTaskData()
const { taskData, editTaskData, loading, error, loadTaskData } = taskDataModule

const taskManagementModule = useTaskManagement(taskDataModule)
const { submitEditTask } = taskManagementModule

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { domainGroups } = storeToRefs(spaceStore)

// 状态
const viewRef = ref<InstanceType<typeof EditView> | null>(null)
const isResubmitting = ref(false)

const hasTaskData = computed(() => taskData.value !== null)
const classificationTopics = computed(() => taskData.value?.space?.classificationTopics ?? [])

// 显示重新提交审核按钮的条件
const showResubmitButton = computed(() => {
  return taskData.value?.approved === 'DISAPPROVED'
})

// Methods
const handleSubmitEdit = async (updatedTaskData: TaskFormSubmitData) => {
  if (isResubmitting.value) {
    await handleSubmitWithReapproval(updatedTaskData)
    return
  }

  await submitEditTask(updatedTaskData)
  navigateToDetail()
}

/** 「保存并重新提交」：先把重新提交这一趟立起来，再让表单自己按原生 submit 走一遍，
 *  提交那条路（`handleSubmitEdit`）才知道该落到重新审核那一支上。 */
const requestResubmit = () => {
  if (!viewRef.value) return
  isResubmitting.value = true
  viewRef.value.requestSubmit()
}

const handleSubmitWithReapproval = async (formData: TaskFormSubmitData) => {
  try {
    await submitEditTask(formData)
    await TasksApi.resubmitTask(taskId)
    toast.success(t('tasks.edit.resubmitted'))

    setTimeout(() => {
      navigateToDetail()
    }, 1500)
  } catch (error) {
    console.error('重新提交审核失败:', error)
  } finally {
    isResubmitting.value = false
  }
}

// 保存 / 取消之后回题目详情。**不是 push**：进来时就是从详情 push 过来的（`Detail.vue`
// 的 `editTask`），出去再 push 一次，身后就多一条详情，按 ← 会落回那张刚保存过的表单。
// 去向是定的（详情），所以走 closeOverlay：身后正是它就退一格，否则 replace 过去。
// 题目详情不在 `App.vue` 的 `keptAlivePages` 里，退回去是重新挂载、重取一遍，不会拿
// 编辑前的旧数据。
const navigateToDetail = () => {
  closeOverlay(router, { name: 'TasksDetail', params: { spaceId: taskData.value?.space?.id, taskId: taskId } })
}

onMounted(async () => {
  await loadTaskData()
  if (taskData.value?.space?.id) {
    spaceStore.setCurrentSpaceId(taskData.value.space.id)
    await spaceData.fetchDomainGroups()
  }
})
</script>

<style scoped>
/* Add any specific styles for the edit page if needed */
</style>
