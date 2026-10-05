<template>
  <!-- 「我的提交」页签：我这一份的逐版提交和评审结果。交新一版的按钮在题目名那一行。 -->
  <div class="sm">
    <v-select
      v-if="showIdentitySelect && identityOptions.length > 1"
      v-model="selectedIdentityModel"
      autocomplete="off"
      :items="identityOptions"
      :label="t('tasks.submissions.identity')"
      hide-details
      variant="outlined"
      density="compact"
      class="sm__identity"
    ></v-select>
    <TaskSubmissionHistoryView
      v-if="currentParticipantId !== null"
      :submissions="submissions"
      :has-more="hasMore"
      :loading-more="loadingMore"
      :refreshing="refreshing"
      :total="total"
      :reviewable="false"
      hide-title
      :highlight-latest="true"
      :outlined="false"
      :empty-text="t('tasks.submissions.empty')"
      @load-more="emit('load-more')"
    />
  </div>
</template>

<script setup lang="ts">
// 「我的提交」这一块**画的那一半**：身份下拉 + 逐版提交记录。
//
// 谁是创建者/管理员、算当前参与者 id、拉提交记录都在容器 `Submissions.vue` 里；
// 这里只吃 props，选身份时往上发。
import type { TaskSubmission } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskSubmissionHistoryView from '@/components/tasks/TaskSubmissionHistoryView.vue'

const { t } = useI18n()

const props = defineProps<{
  showIdentitySelect: boolean
  identityOptions: { title: string; value: number; disabled: boolean }[]
  selectedIdentity: number | null
  currentParticipantId: number | null
  submissions: TaskSubmission[]
  hasMore: boolean
  loadingMore: boolean
  refreshing: boolean
  total: number
}>()

const emit = defineEmits<{
  'update:selectedIdentity': [value: number | null]
  'load-more': []
}>()

const selectedIdentityModel = computed({
  get: () => props.selectedIdentity,
  set: (value: number | null) => emit('update:selectedIdentity', value),
})
</script>

<style scoped>
.sm__identity {
  max-width: 240px;
  margin-bottom: 16px;
}
</style>
