<template>
  <AnswerListView
    :answers="answers"
    :question="questionData"
    :refreshing="refreshing"
    :can-accept="canAccept"
    :failed="failed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    @retry="retry"
    @upvote="actions.upvote($event)"
    @downvote="actions.downvote($event)"
    @cancel-vote="actions.cancelVote($event)"
    @favorite="actions.favorite($event)"
    @accept="actions.accept($event)"
  />
</template>

<script setup lang="ts">
// 一道题下面那串回答**取数的那一半**：翻页与刷新（`composables/useAnswerList`）、
// 每一颗上的写操作（`composables/useAnswerActions`）、题主判断，以及从问题详情
// 注入进来的那道题。画的那一半在 `AnswerListView.vue`，只认 props、只发事件。
//
// 外部接口没变：还是吃一个 `questionId` prop（题详情容器 `provide` 的那道题由这里
// `inject` 拿）。
import type { Question } from '@/types'

import { computed, inject, ref } from 'vue'

import { useAnswerActions } from '@/composables/useAnswerActions'
import { useAnswerList } from '@/composables/useAnswerList'

import AnswerListView from './AnswerListView.vue'

import { questionDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { currentUserId } from '@/services/account'

const props = defineProps<{
  questionId: number
}>()

// 容器没 provide 时退回一张空题：列表照样画，只是没有题主判断与采纳。
const questionData = inject(questionDataInjectionKey, ref<Question | null>(null))

const { answers, refreshing, retry, refresh, error } = useAnswerList(() => props.questionId)
const actions = useAnswerActions({ question: () => questionData.value, refresh })

const failed = computed(() => error.value !== null)
const failureReason = computed(() => loadFailureReason(error.value))
const forbidden = computed(() => isForbidden(error.value))
const canAccept = computed(() => !!questionData.value && questionData.value.author.id === currentUserId.value)
</script>
