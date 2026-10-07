<template>
  <v-sheet flat rounded="lg">
    <slot name="header"></slot>
    <template v-if="refreshing">
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
    </template>
    <template v-else>
      <template v-if="data.length === 0">
        <blank-page />
      </template>
      <template v-else>
        <answer-card v-for="answer in data" :key="answer.id" :answer="answer" :question="questionData" />
      </template>
    </template>
  </v-sheet>
</template>

<script setup lang="ts">
import { inject, onMounted } from 'vue'

import { useAnswerPaging } from '@/composables/useAnswerPaging'

import AnswerCard from '../answer/AnswerCard.vue'
import BlankPage from '../common/BlankPage.vue'

import { questionDataInjectionKey } from '@/keys'

const props = defineProps<{
  questionId: number
}>()

const questionData = inject(questionDataInjectionKey)

// 取数在 `useAnswerPaging`（组件不吃 API 层），这里只用它交回来的那几个状态。
const { data, refresh, loadMore, refreshing, loadingMore } = useAnswerPaging(() => props.questionId)

onMounted(async () => {
  await refresh()
})
</script>
