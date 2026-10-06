<template>
  <TaskSubmissionHistoryView
    :submissions="submissions"
    :has-more="hasMore"
    :loading-more="loadingMore"
    :refreshing="refreshing"
    :total="total"
    :submitting="submitting"
    :reviewable="reviewable"
    :show-history="showHistory"
    :hide-title="hideTitle"
    :hide-history-title="hideHistoryTitle"
    :title="title"
    :history-title="historyTitle"
    :empty-text="emptyText"
    :outlined="outlined"
    :highlight-latest="highlightLatest"
    :is-dialog="isDialog"
    @load-more="loadMore"
    @submit-review="onReview"
    @cancel-review="onCancelReview"
  />
</template>

<script setup lang="ts">
// 容器：翻页取提交记录、提交/改/撤回评审都在这儿；画面交给 TaskSubmissionHistoryView。
import type { PostTaskSubmissionReviewRequestData } from '@/network/api/tasks/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { usePaging } from '@/utils/paging'

import TaskSubmissionHistoryView from './TaskSubmissionHistoryView.vue'

import { TasksApi } from '@/network/api/tasks'

const { t } = useI18n()

interface Props {
  taskId: number
  participantId: number
  reviewable?: boolean
  showHistory?: boolean
  hideTitle?: boolean
  hideHistoryTitle?: boolean
  title?: string
  historyTitle?: string
  emptyText?: string
  outlined?: boolean
  highlightLatest?: boolean
  isDialog?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  reviewable: false,
  showHistory: true,
  hideTitle: false,
  hideHistoryTitle: false,
  title: '',
  historyTitle: '',
  emptyText: '',
  outlined: false,
  highlightLatest: false,
  isDialog: false,
})

const {
  data: submissions,
  refresh,
  loadMore,
  hasMore,
  refreshing,
  loadingMore,
  total,
} = usePaging(async (pageStart) => {
  const { data } = await TasksApi.listSubmissions(props.taskId, props.participantId, {
    allVersions: true,
    sort_by: 'createdAt',
    sort_order: 'desc',
    pageStart: pageStart,
    pageSize: 10,
    queryReview: true,
  })
  return { data: data.submissions, page: data.page }
})

const latestSubmission = computed(() => submissions.value[0])

const submitting = ref(false)

const onReview = async (values: PostTaskSubmissionReviewRequestData) => {
  const latest = latestSubmission.value
  if (!latest) return
  submitting.value = true
  try {
    if (latest.review && latest.review.reviewed) {
      await TasksApi.patchSubmissionReview(props.taskId, props.participantId, latest.id, values)
      toast.success(t('tasks.submissionHistory.reviewUpdated'))
    } else {
      await TasksApi.postSubmissionReview(props.taskId, props.participantId, latest.id, values)
      toast.success(t('tasks.submissionHistory.reviewed'))
    }
  } catch (error) {
    toast.error(
      latest.review && latest.review.reviewed
        ? t('tasks.submissionHistory.reviewUpdateFailed')
        : t('tasks.submissionHistory.reviewFailed')
    )
    console.error(error)
  } finally {
    submitting.value = false
    refresh()
  }
}

const onCancelReview = async () => {
  const latest = latestSubmission.value
  if (latest?.review) {
    try {
      await TasksApi.deleteSubmissionReview(props.taskId, props.participantId, latest.id)
      toast.success(t('tasks.submissionHistory.reviewCanceled'))
    } catch (error) {
      toast.error(t('tasks.submissionHistory.reviewCancelFailed'))
      console.error(error)
    } finally {
      refresh()
    }
  }
}

onMounted(refresh)

watch(() => [props.taskId, props.participantId], refresh)

defineExpose({
  refresh,
})
</script>
