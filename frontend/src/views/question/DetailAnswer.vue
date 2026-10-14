<template>
  <div>
    <BaseButton v-if="questionData" exact block kind="secondary" class="mb-4" :to="{ name: 'QuestionAnswerList' }">
      {{
        t('questions.detail.buttons.allAnswers', {
          count: questionData.answer_count,
        })
      }}
    </BaseButton>
    <v-skeleton-loader v-else type="heading" class="mb-4" />
    <answer-card
      v-if="answerData"
      :answer="answerData"
      :question="questionData"
      :can-accept="canAccept"
      :default-avatar-id="globalDefaultAvatarId"
      @accept="accept"
      @upvote="upvote"
      @downvote="downvote"
      @cancel-vote="cancelVote"
      @favorite="favorite"
    />
    <v-skeleton-loader v-else type="list-item-avatar, paragraph, button@2" />
  </div>
</template>

<script setup lang="ts">
import type { Answer, Question } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { useAnswerActions } from '@/composables/useAnswerActions'
import { globalDefaultAvatarId } from '@/composables/useChosenAvatar'

import { AnswersApi } from '@/api/answers'
import AnswerCard from '@/components/answer/AnswerCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

const route = useRoute()

const questionData = ref<Question | null>(null)
const answerData = ref<Answer | null>(null)

const questionId = computed(() => parseInt(route.params.questionId as string))
const answerId = computed(() => parseInt(route.params.answerId as string))

// 卡片只画状态，这几件事在这里发请求（连同「能不能采纳」的判据）。
const { canAccept, accept, upvote, downvote, cancelVote, favorite } = useAnswerActions(questionData)

onMounted(async () => {
  const { question, answer } = await AnswersApi.getAnswerDetail(questionId.value, answerId.value)
  questionData.value = question
  answerData.value = answer
})
</script>
