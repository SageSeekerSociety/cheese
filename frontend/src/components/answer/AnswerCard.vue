<template>
  <AnswerCardView
    :answer="answer"
    :question="question"
    :can-accept="canAccept"
    @upvote="actions.upvote(answer)"
    @downvote="actions.downvote(answer)"
    @cancel-vote="actions.cancelVote(answer)"
    @favorite="actions.favorite(answer)"
    @accept="actions.accept(answer)"
  />
</template>

<script setup lang="ts">
// 一条回答**取数的那一半**：那几个写操作（`composables/useAnswerActions`）、
// 采纳后让列表重刷（`refreshInjectionKey`）、以及「当前登录者是不是题主」的判断
// 都在这儿。画的那一半在 `AnswerCardView.vue`，只认 props、只发事件。
//
// 外部接口没变：还是吃 `answer` / `question` 两个 prop。
import type { Answer, Question } from '@/types'

import { computed, inject, toRefs } from 'vue'

import { useAnswerActions } from '@/composables/useAnswerActions'

import AnswerCardView from './AnswerCardView.vue'

import { refreshInjectionKey } from '@/keys'
import { currentUserId } from '@/services/account'

const props = withDefaults(
  defineProps<{
    answer: Answer
    question?: Question | null
  }>(),
  {
    question: null,
  }
)

const { answer, question } = toRefs(props)

const refresh = inject(refreshInjectionKey, () => {})

const actions = useAnswerActions({ question: () => question.value, refresh })

// 采纳按钮只有题主看得见；题还没落地时也不画。
const canAccept = computed(() => !!question.value && question.value.author.id === currentUserId.value)
</script>
