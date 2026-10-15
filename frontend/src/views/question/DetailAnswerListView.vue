<script setup lang="ts">
// 「全部回答」这一页的画面：一份答案列表，含还没读回来时的骨架屏和空板。取数和那几个
// 动作（采纳、投票、收藏）都在容器 `DetailAnswerList.vue` 那边，这里只收结果、把要点
// 的事交上去。
import type { Answer, Question } from '@/types'

import AnswerCard from '@/components/answer/AnswerCard.vue'
import BlankPage from '@/components/common/BlankPage.vue'

withDefaults(
  defineProps<{
    answers: Answer[]
    question?: Question | null
    /** 还在读第一页：画骨架屏，不画「还没有回答」。 */
    refreshing: boolean
    /** 只有提问的人能采纳。 */
    canAccept: boolean
    /** 全局默认头像的 id；`null` 是还没问到。 */
    defaultAvatarId?: number | null
  }>(),
  {
    question: null,
    defaultAvatarId: null,
  }
)

const emit = defineEmits<{
  accept: [answer: Answer]
  upvote: [answer: Answer]
  downvote: [answer: Answer]
  'cancel-vote': [answer: Answer]
  favorite: [answer: Answer]
}>()
</script>

<template>
  <v-sheet flat rounded="lg">
    <template v-if="refreshing">
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
    </template>
    <template v-else>
      <template v-if="answers.length === 0">
        <blank-page />
      </template>
      <template v-else>
        <answer-card
          v-for="answer in answers"
          :key="answer.id"
          :answer="answer"
          :question="question"
          :can-accept="canAccept"
          :default-avatar-id="defaultAvatarId"
          @accept="emit('accept', $event)"
          @upvote="emit('upvote', $event)"
          @downvote="emit('downvote', $event)"
          @cancel-vote="emit('cancel-vote', $event)"
          @favorite="emit('favorite', $event)"
        />
      </template>
    </template>
  </v-sheet>
</template>
