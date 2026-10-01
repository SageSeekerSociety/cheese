<template>
  <v-container>
    <v-card outlined class="pa-4">
      <v-card-title class="text-h5 mb-4">
        <v-icon left class="mr-2">mdi-pencil</v-icon>
        {{ t('tasks.detail.editTask') }}
      </v-card-title>
      <v-divider class="mb-4"></v-divider>
      <LoadingErrorContainer v-if="loading || error" :loading="loading" :error="error" @retry="loadTaskData" />
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
            <v-btn variant="text" :disabled="isSubmitting || isResubmitting" @click="navigateToDetail">{{
              t('global.cancel')
            }}</v-btn>
            <v-btn color="primary" :loading="isSubmitting" type="submit">{{ t('tasks.edit.saveChanges') }}</v-btn>
            <v-btn
              v-if="showResubmitButton"
              color="success"
              :loading="isResubmitting"
              :disabled="isSubmitting"
              type="button"
              @click="submitWithReapproval"
            >
              {{ t('tasks.edit.saveAndResubmit') }}
            </v-btn>
          </div>
        </template>
      </TaskForm>

      <!-- 提交审核成功提示 -->
      <v-snackbar v-model="showResubmitSuccess" :timeout="3000">{{ t('tasks.edit.resubmitted') }}</v-snackbar>
    </v-card>
  </v-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { LoadingErrorContainer } from './components'
import { useTaskData, useTaskManagement } from './composables'

import TaskForm from '@/components/tasks/TaskForm.vue'
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
const showResubmitSuccess = ref(false)
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
    showResubmitSuccess.value = true

    setTimeout(() => {
      navigateToDetail()
    }, 1500)
  } catch (error) {
    console.error('重新提交审核失败:', error)
  } finally {
    isResubmitting.value = false
  }
}

const navigateToDetail = () => {
  router.push({ name: 'TasksDetail', params: { spaceId: taskData.value?.space?.id, taskId: taskId } })
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
