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
      <!-- A failed read trades the form for the error (docs/design-system.md §3.10). -->
      <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="emit('retry')" />
      <TaskForm
        v-else-if="hasTaskData"
        ref="taskFormRef"
        :initial-data="initialData"
        :submit-button-text="t('tasks.edit.saveChanges')"
        is-editing
        :classification-topics="classificationTopics"
        :domain-groups="domainGroups"
        :description-format="initialData?.descriptionFormat"
        :original-description="initialData?.originalDescription"
        @submit="emit('submit', $event)"
        @cancel="emit('cancel')"
      >
        <template #buttons="{ isSubmitting }">
          <div class="d-flex gap-4">
            <BaseButton kind="ghost" :disabled="isSubmitting || isResubmitting" @click="emit('cancel')">{{
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
              @click="emit('resubmit')"
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
// 改题页的画面：加载圈、读失败提示、以及那张发题/改题表单。取数、保存、跳转都在
// 容器 `Edit.vue` 里 —— 这里只吃 props、只往上发事件。
import type { DomainGroup, TaskFormSubmitData, Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

const { t } = useI18n()

defineProps<{
  loading: boolean
  error: string | null
  /** 题目数据到位了才画表单；没有就什么都不画（加载中和读失败已经各自占了一格）。 */
  hasTaskData: boolean
  initialData: Partial<TaskFormSubmitData> | null
  classificationTopics: Topic[]
  domainGroups: DomainGroup[]
  isResubmitting: boolean
  showResubmitButton: boolean
}>()

const emit = defineEmits<{
  retry: []
  submit: [data: TaskFormSubmitData]
  cancel: []
  resubmit: []
}>()

const taskFormRef = ref<InstanceType<typeof TaskForm> | null>(null)

// 「保存并重新提交」那颗按钮要把表单原样提交一次：真正按下原生 submit 由容器催
// （它先把自己那一份 `isResubmitting` 立起来，提交那条路才知道该走重新审核）。
defineExpose({
  requestSubmit: () => (taskFormRef.value?.$el as HTMLFormElement | undefined)?.requestSubmit(),
})
</script>

<style scoped>
/* Add any specific styles for the edit page if needed */
</style>
