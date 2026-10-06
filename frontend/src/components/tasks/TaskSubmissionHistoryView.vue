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
              <v-text-field
                v-model.number="score"
                :label="t('tasks.submissionHistory.score')"
                type="number"
                min="0"
                max="100"
                v-bind="scoreProps"
              />
              <v-textarea
                v-model="comment"
                autocomplete="off"
                :label="t('tasks.submissionHistory.comment')"
                v-bind="commentProps"
              />
            </v-form>
          </template>
          <template #actions>
            <BaseButton v-if="latestSubmission.review?.reviewed" kind="danger" @click="emit('cancel-review')">
              {{ t('tasks.submissionHistory.cancelReview') }}
            </BaseButton>
            <BaseButton kind="primary" :loading="submitting" :disabled="submitting" @click="submitReview">
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
        @load-more="emit('load-more')"
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
// 「提交记录」这一块**画的那一半**：最新一版、评审表单、逐版历史列表。
//
// 翻页取数、提交/撤回评审都在容器 `TaskSubmissionHistory.vue` 里；这里只吃 props，
// 把评审表单收好的值往上发。表单自身的校验与字段状态是纯 UI 状态，留在这里。
import type { TaskSubmission, TaskSubmissionReview } from '@/types'

import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import dayjs from 'dayjs'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import SubmissionContentCard from './SubmissionContentCard.vue'
import SubmissionReviewStatus from './SubmissionReviewStatus.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const { t } = useI18n()

/** 评审表单收好的值，交给容器去打接口。三栏都到齐了才发：`score` 在表单里是必填，
 *  `accepted` / `comment` 由 schema 的默认值补齐（`true` / 空串），所以发出去的一定是
 *  完整的一份，而不是「可能少一栏」—— 接口那边要的也是完整的一份。 */
interface ReviewValues {
  accepted: boolean
  score: number
  comment: string
}

const props = withDefaults(
  defineProps<{
    submissions: TaskSubmission[]
    hasMore: boolean
    loadingMore: boolean
    refreshing: boolean
    total: number
    /** 评审动作进行中 —— 由容器驱动。 */
    submitting?: boolean
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
  }>(),
  {
    submitting: false,
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
  }
)

const emit = defineEmits<{
  'load-more': []
  'submit-review': [values: ReviewValues]
  'cancel-review': []
}>()

const latestSubmission = computed(() => props.submissions[0])

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

const { handleSubmit, defineField, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      accepted: z.boolean().optional().default(true),
      score: z.number().min(0).max(100),
      comment: z.string().max(255).optional().default(''),
    })
  ),
})

const [accepted, acceptedProps] = defineField('accepted', vuetifyConfig)
const [score, scoreProps] = defineField('score', vuetifyConfig)
const [comment, commentProps] = defineField('comment', vuetifyConfig)

const submitReview = handleSubmit((values) => {
  emit('submit-review', values as ReviewValues)
})

watch(latestSubmission, (newVal) => {
  if (props.reviewable && newVal?.review) {
    if (newVal.review.reviewed) {
      accepted.value = newVal.review.detail.accepted
      score.value = newVal.review.detail.score
      comment.value = newVal.review.detail.comment
    } else {
      resetForm()
    }
  }
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
