<template>
  <div class="d-flex flex-column ga-2 w-100 align-stretch">
    <v-card v-if="submissions.length" flat rounded="lg" :class="{ border: outlined, 'mb-4': !isDialog }">
      <template v-if="!hideTitle" #title> {{ title || t('tasks.submissionHistory.latest') }} </template>
      <template #text>
        <v-card border flat rounded="lg" class="pa-4" :class="{ 'gradient-card': highlightLatest }">
          <div class="d-flex flex-row align-center mb-2">
            <v-chip color="primary" variant="tonal" size="small"> #{{ latestSubmission.version }} </v-chip>
            <span class="ml-2">
              {{
                t('tasks.submissionHistory.submittedAt', {
                  time: dayjs(latestSubmission.createdAt).format('YYYY-MM-DD HH:mm'),
                })
              }}
            </span>
            <div class="flex-grow-1"></div>
            <v-chip
              v-if="latestSubmission.review"
              size="small"
              variant="tonal"
              :color="calcSubmissionReviewColor(latestSubmission.review)"
            >
              {{ calcSubmissionReviewText(latestSubmission.review) }}
            </v-chip>
          </div>
          <div v-for="(content, index) in latestSubmission.content" :key="index">
            <SubmissionContentCard
              :content="content"
              :class="{ 'mb-2': index !== latestSubmission.content.length - 1 }"
            />
          </div>
        </v-card>

        <v-card v-if="reviewable" flat rounded="lg" border class="mt-4">
          <template #title>
            {{
              latestSubmission.review?.reviewed
                ? t('tasks.submissionHistory.editReview')
                : t('tasks.submissionHistory.review')
            }}
          </template>
          <template #text>
            <v-form @submit.prevent="submitReview">
              <v-radio-group
                v-model="accepted"
                inline
                :label="t('tasks.submissionHistory.passed')"
                v-bind="acceptedProps"
              >
                <v-radio :label="t('tasks.submissionHistory.accept')" :value="true"></v-radio>
                <v-radio :label="t('tasks.submissionHistory.reject')" :value="false"></v-radio>
              </v-radio-group>
              <!-- A score is what a pass is worth (the result reads 「已通过 N 分」); a
                   rejection reads 「已驳回」 with no number, so it asks for none. -->
              <BaseField
                v-if="accepted !== false"
                :label="t('tasks.submissionHistory.score')"
                required
                :error="scoreError"
                class="mb-4"
              >
                <template #default="{ id, describedby, invalid, required }">
                  <v-text-field
                    :id="id"
                    :model-value="score"
                    :aria-describedby="describedby"
                    :aria-invalid="invalid"
                    :aria-required="required"
                    :error="invalid"
                    type="number"
                    min="0"
                    max="100"
                    variant="outlined"
                    density="comfortable"
                    hide-details
                    @update:model-value="score = $event === '' ? undefined : Number($event)"
                  />
                </template>
              </BaseField>
              <v-textarea
                v-model="comment"
                autocomplete="off"
                :label="t('tasks.submissionHistory.comment')"
                v-bind="commentProps"
              />
            </v-form>
          </template>
          <template #actions>
            <BaseButton v-if="latestSubmission.review?.reviewed" kind="danger" @click="cancelReview">
              {{ t('tasks.submissionHistory.cancelReview') }}
            </BaseButton>
            <BaseButton kind="primary" :loading="isSubmitting" :disabled="isSubmitting" @click="submitReview">
              {{ t('tasks.submissionHistory.submit') }}
            </BaseButton>
          </template>
        </v-card>
        <SubmissionReviewStatus v-else-if="latestSubmission.review" class="mt-4" :review="latestSubmission.review" />
      </template>
    </v-card>

    <v-card v-if="showHistory" flat rounded="lg" :class="{ border: outlined }">
      <template v-if="submissions.length && !hideHistoryTitle" #title>
        {{ historyTitle || t('tasks.submissionHistory.history') }}
      </template>
      <infinite-scroll
        :has-more="hasMore"
        :loading="loadingMore"
        :initial-loading="refreshing"
        :is-empty="submissions.length <= 1"
        :shown="submissions.length"
        :total="total"
        force-manual
        @load-more="loadMore"
      >
        <template #empty>
          <BaseEmptyState size="compact" icon="" :title="emptyText || t('tasks.submissionHistory.empty')" />
        </template>
        <v-expansion-panels>
          <template v-for="submission in submissions.slice(1)" :key="submission.id">
            <v-expansion-panel :elevation="0" ripple>
              <template #title>
                <div class="d-flex flex-row align-center">
                  <v-chip color="primary" variant="tonal" size="small"> #{{ submission.version }} </v-chip>
                  <span class="ml-2">
                    {{
                      t('tasks.submissionHistory.submittedBy', {
                        name: submission.submitter.nickname,
                        time: dayjs(submission.createdAt).format('YYYY-MM-DD HH:mm'),
                      })
                    }}
                  </span>
                  <div class="flex-grow-1"></div>
                  <v-chip
                    v-if="submission.review"
                    size="small"
                    variant="tonal"
                    :color="calcSubmissionReviewColor(submission.review)"
                  >
                    {{ calcSubmissionReviewText(submission.review) }}
                  </v-chip>
                </div>
              </template>
              <template #text>
                <div v-for="(content, index) in submission.content" :key="index">
                  <SubmissionContentCard
                    :content="content"
                    :class="{ 'mb-2': index !== submission.content.length - 1 }"
                  />
                </div>
                <SubmissionReviewStatus v-if="submission.review" class="mt-4" :review="submission.review" />
              </template>
            </v-expansion-panel>
          </template>
        </v-expansion-panels>
      </infinite-scroll>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import type { TaskSubmissionReview } from '@/types'

import { computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import dayjs from 'dayjs'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'
import { usePaging } from '@/utils/paging'

import SubmissionContentCard from './SubmissionContentCard.vue'
import SubmissionReviewStatus from './SubmissionReviewStatus.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseField from '@/components/base/BaseField.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
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

const calcSubmissionReviewColor = (review: TaskSubmissionReview) => {
  if (!review) {
    return 'text'
  }
  if (!review.reviewed) {
    return 'text'
  }
  if (review.detail.accepted) {
    return 'success'
  }
  return 'error'
}

const calcSubmissionReviewText = (review: TaskSubmissionReview) => {
  if (!review || !review.reviewed) {
    return t('tasks.submissionHistory.notReviewed')
  }
  return review.detail.accepted ? t('tasks.submissionHistory.accept') : t('tasks.submissionHistory.reject')
}

const { handleSubmit, defineField, isSubmitting, resetForm, errors } = useForm({
  initialValues: { accepted: true },
  validationSchema: toTypedSchema(
    z
      .object({
        accepted: z.boolean().default(true),
        score: z.number().optional(),
        comment: z.string().max(255).optional().default(''),
      })
      .superRefine((values, ctx) => {
        if (!values.accepted) return
        const { score } = values
        if (score === undefined || !Number.isInteger(score) || score < 0 || score > 100) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['score'],
            message: t('tasks.submissionHistory.scoreRequired'),
          })
        }
      })
  ),
})

const [accepted, acceptedProps] = defineField('accepted', vuetifyConfig)
const [score] = defineField('score')
const [comment, commentProps] = defineField('comment', vuetifyConfig)
const scoreError = computed(() => errors.value.score)

const submitReview = handleSubmit(async (form) => {
  // The review row keeps a score either way; a rejection's is never shown, so it is 0.
  const values = { accepted: form.accepted, score: form.accepted ? form.score ?? 0 : 0, comment: form.comment }
  if (latestSubmission.value.review && latestSubmission.value.review.reviewed) {
    try {
      await TasksApi.patchSubmissionReview(props.taskId, props.participantId, latestSubmission.value.id, values)
      toast.success(t('tasks.submissionHistory.reviewUpdated'))
    } catch (error) {
      toast.error(t('tasks.submissionHistory.reviewUpdateFailed'))
      console.error(error)
    } finally {
      refresh()
    }
  } else {
    try {
      await TasksApi.postSubmissionReview(props.taskId, props.participantId, latestSubmission.value.id, values)
      toast.success(t('tasks.submissionHistory.reviewed'))
    } catch (error) {
      toast.error(t('tasks.submissionHistory.reviewFailed'))
      console.error(error)
    } finally {
      refresh()
    }
  }
})

const cancelReview = async () => {
  if (latestSubmission.value.review) {
    try {
      await TasksApi.deleteSubmissionReview(props.taskId, props.participantId, latestSubmission.value.id)
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

watch(latestSubmission, (newVal) => {
  if (props.reviewable && newVal.review) {
    if (newVal.review.reviewed) {
      accepted.value = newVal.review.detail.accepted
      score.value = newVal.review.detail.score
      comment.value = newVal.review.detail.comment
    } else {
      resetForm()
    }
  }
})

defineExpose({
  refresh,
})
</script>

<style scoped>
.gradient-card {
  background: linear-gradient(to right bottom, rgba(var(--v-theme-primary), 0.05), rgba(var(--v-theme-primary), 0.01));
  border: 1px solid rgba(var(--v-theme-primary), 0.1);
}

.border {
  border: 1px solid var(--line);
}
</style>
