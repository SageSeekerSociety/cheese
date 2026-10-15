<script setup lang="ts">
// 单份回答那一页的容器：从地址上认这是哪道题的第几份回答，把它读回来，再把画面交给
// `DetailAnswerView.vue`。采纳/投票/收藏这几个动作也在这里发请求（连同「能不能采纳」的判据）。
import type { Answer, Question } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAnswerActions } from '@/composables/useAnswerActions'
import { globalDefaultAvatarId } from '@/composables/useChosenAvatar'

import { AnswersApi } from '@/api/answers'
import DetailAnswerView from '@/views/question/DetailAnswerView.vue'

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

<template>
  <DetailAnswerView
    :question-data="questionData"
    :answer-data="answerData"
    :can-accept="canAccept"
    :default-avatar-id="globalDefaultAvatarId"
    @accept="accept"
    @upvote="upvote"
    @downvote="downvote"
    @cancel-vote="cancelVote"
    @favorite="favorite"
  />
</template>
