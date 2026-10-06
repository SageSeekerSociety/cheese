<template>
  <DetailAnswerView
    :question="questionData"
    :answer="answerData"
    :can-accept="canAccept"
    :failed="failed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    @retry="load"
    @upvote="run(actions.upvote)"
    @downvote="run(actions.downvote)"
    @cancel-vote="run(actions.cancelVote)"
    @favorite="run(actions.favorite)"
    @accept="run(actions.accept)"
  />
</template>

<script setup lang="ts">
// 单条回答页（`/questions/:questionId/answers/:answerId`）：读路由、取这条回答与它挂的
// 那道题、读失败态、写操作都在这儿；画的那一半在 `DetailAnswerView.vue`。
import type { Answer, Question } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAnswerActions } from '@/composables/useAnswerActions'

import DetailAnswerView from './DetailAnswerView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { AnswersApi } from '@/network/api/answers'
import { currentUserId } from '@/services/account'

const route = useRoute()

const questionData = ref<Question | null>(null)
const answerData = ref<Answer | null>(null)
/** 非空表示这一次没读到：`answerData` 为 null 是假的，不是「没有这条回答」。 */
const loadError = ref<unknown>(null)

const questionId = computed(() => parseInt(route.params.questionId as string))
const answerId = computed(() => parseInt(route.params.answerId as string))

const actions = useAnswerActions({ question: () => questionData.value })

const canAccept = computed(() => !!questionData.value && questionData.value.author.id === currentUserId.value)
const failed = computed(() => loadError.value !== null)
const failureReason = computed(() => loadFailureReason(loadError.value))
const forbidden = computed(() => isForbidden(loadError.value))

async function load() {
  loadError.value = null
  try {
    const {
      data: { question, answer },
    } = await AnswersApi.getAnswerDetail(questionId.value, answerId.value)
    questionData.value = question
    answerData.value = answer
  } catch (error) {
    loadError.value = error
  }
}

/** 卡片上的写操作都对着这一条回答来（这一屏只有它）。 */
function run(action: (answer: Answer) => Promise<void>) {
  if (answerData.value) void action(answerData.value)
}

onMounted(load)
</script>
