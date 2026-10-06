<template>
  <div>
    <!-- A failed load replaces this screen: it must not sit on skeletons, nor pose the "view all answers" button as a live question. -->
    <BaseLoadError
      v-if="failed"
      :title="t('questions.answer.loadFailed')"
      :error="failureReason"
      :forbidden="forbidden"
      @retry="$emit('retry')"
    />

    <template v-else>
      <BaseButton v-if="question" exact block kind="secondary" class="mb-4" :to="{ name: 'QuestionAnswerList' }">
        {{
          t('questions.detail.buttons.allAnswers', {
            count: question.answer_count,
          })
        }}
      </BaseButton>
      <v-skeleton-loader v-else type="heading" class="mb-4" />
      <answer-card-view
        v-if="answer"
        :answer="answer"
        :question="question"
        :can-accept="canAccept"
        @upvote="$emit('upvote')"
        @downvote="$emit('downvote')"
        @cancel-vote="$emit('cancelVote')"
        @favorite="$emit('favorite')"
        @accept="$emit('accept')"
      />
      <v-skeleton-loader v-else type="list-item-avatar, paragraph, button@2" />
    </template>
  </div>
</template>

<script setup lang="ts">
// 单条回答页（`/questions/:questionId/answers/:answerId`）**画的那一半**：只认 props、
// 只往上发事件。取数（`AnswersApi.getAnswerDetail`）、题主判断、读失败态与写操作都在
// 容器 `DetailAnswer.vue` 里。
import type { Answer, Question } from '@/types'

import { useI18n } from 'vue-i18n'

import AnswerCardView from '@/components/answer/AnswerCardView.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

const { t } = useI18n()

defineProps<{
  question: Question | null
  answer: Answer | null
  /** 当前登录者是不是题主：决定卡上画不画采纳按钮。 */
  canAccept: boolean
  /** 这一次没读到：`question` / `answer` 为 null 是假的，不是「没有这条回答」。 */
  failed: boolean
  failureReason: string | null
  /** 401/403：不给看，不给重试。 */
  forbidden: boolean
}>()

defineEmits<{
  retry: []
  upvote: []
  downvote: []
  cancelVote: []
  favorite: []
  accept: []
}>()
</script>
