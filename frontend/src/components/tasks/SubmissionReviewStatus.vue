<template>
  <v-alert :type="alertType" :title="alertTitle" :text="alertText" variant="tonal"></v-alert>
</template>

<script setup lang="ts">
import type { TaskSubmissionReview } from '@/types'

import { computed, toRefs } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps<{
  review: TaskSubmissionReview
}>()

const { review } = toRefs(props)

const alertType = computed(() => {
  if (!review.value || !review.value.reviewed) {
    return 'info'
  }
  return review.value.detail.accepted ? 'success' : 'error'
})

const alertTitle = computed(() => {
  if (!review.value || !review.value.reviewed) {
    return t('tasks.reviewStatus.pending')
  }
  return review.value.detail.accepted
    ? t('tasks.reviewStatus.passed', { score: review.value.detail.score })
    : t('tasks.reviewStatus.rejected')
})

const alertText = computed(() => {
  if (!review.value || !review.value.reviewed) {
    return undefined
  }
  return t('tasks.reviewStatus.comment', { comment: review.value.detail.comment })
})
</script>
