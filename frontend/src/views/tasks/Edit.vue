<template>
  <v-container>
    <v-card outlined class="pa-4">
      <v-card-title class="text-h5 mb-4">
        <v-icon left class="mr-2">mdi-pencil</v-icon>
        {{ t('tasks.detail.editTask') }}
      </v-card-title>
      <v-divider class="mb-4"></v-divider>
      <div v-if="loading" class="py-12 text-center">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <!-- 读失败就把这块表单换成错误（docs/design-system.md §3.10）。 -->
      <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="loadTaskData" />
      <TaskForm
        v-else-if="taskData"
        ref="taskFormRef"
        :initial-data="editTaskData"
        :submit-button-text="t('tasks.edit.saveChanges')"
        is-editing
        :classification-topics="taskData.space?.classificationTopics || []"
        :domain-groups="domainGroups"
        :description-format="editTaskData.descriptionFormat"
        :original-description="editTaskData.originalDescription"
        @submit="handleSubmitEdit"
        @cancel="navigateToDetail"
      >
        <template #buttons="{ isSubmitting }">
          <div class="d-flex gap-4">
            <BaseButton kind="ghost" :disabled="isSubmitting || isResubmitting" @click="navigateToDetail">{{
              t('global.cancel')
            }}</BaseButton>
            <BaseButton kind="primary" :loading="isSubmitting" type="submit">{{
              t('tasks.edit.saveChanges')
            }}</BaseButton>
            <BaseButton
              v-if="showResubmitButton"
              kind="secondary"
              :loading="isResubmitting"
              :disabled="isSubmitting"
              type="button"
              @click="submitWithReapproval"
            >
              {{ t('tasks.edit.saveAndResubmit') }}
            </BaseButton>
          </div>
        </template>
      </TaskForm>
    </v-card>
  </v-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { useTaskData, useTaskManagement } from './composables'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
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
const isResubmitting = ref(false)
const taskFormRef = ref<InstanceType<typeof TaskForm> | null>(null)

// 显示重新提交审核按钮的条件
const showResubmitButton = computed(() => {
  return taskData.value?.approved === 'DISAPPROVED'
})

// Methods
const handleSubmitEdit = async (updatedTaskData: any) => {
  if (isResubmitting.value) {
    await handleSubmitWithReapproval(updatedTaskData)
    return
  }

  await submitEditTask(updatedTaskData)
  navigateToDetail()
}

const submitWithReapproval = async () => {
  if (!taskFormRef.value) return
  isResubmitting.value = true
  const form = taskFormRef.value.$el as HTMLFormElement
  form.requestSubmit()
}

const handleSubmitWithReapproval = async (formData: any) => {
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
