<template>
  <DetailAnswerListView
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
// 问题详情下的「全部回答」（`/questions/:questionId`）：取数、翻页、每一颗上的写操作、
// 题主判断与读失败态都在这儿；画的那一半在 `DetailAnswerListView.vue`。
//
// 它自己是被 `Detail.vue` 的 `<router-view>` 渲染的子路由，那道题由 `Detail.vue`
// `provide` 下来。
import type { Question } from '@/types'

import { computed, inject, ref } from 'vue'

import { useAnswerActions } from '@/composables/useAnswerActions'
import { useAnswerList } from '@/composables/useAnswerList'
import { useNavigation } from '@/composables/useNavigation'

import DetailAnswerListView from './DetailAnswerListView.vue'

import { questionDataInjectionKey } from '@/keys'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { currentUserId } from '@/services/account'

const nav = useNavigation()

const questionId = computed(() => Number(nav?.route?.params?.questionId))

const questionData = inject(questionDataInjectionKey, ref<Question | null>(null))

const { answers, refreshing, retry, refresh, error } = useAnswerList(() => questionId.value)
const actions = useAnswerActions({ question: () => questionData.value, refresh })

const failed = computed(() => error.value !== null)
const failureReason = computed(() => loadFailureReason(error.value))
const forbidden = computed(() => isForbidden(error.value))
const canAccept = computed(() => !!questionData.value && questionData.value.author.id === currentUserId.value)
</script>
