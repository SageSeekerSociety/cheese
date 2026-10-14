<script setup lang="ts">
// 单份回答那一页的画面：头上「全部回答」那颗按钮、一张回答卡片，各自还没读回来时画骨架屏。
// 读哪一份、能不能采纳、采纳/投票/收藏的请求都在容器 `DetailAnswer.vue` 那边，这里只收结果、
// 把要点的事连同是哪一份回答交上去。
import type { Answer, Question } from '@/types'

import { useI18n } from 'vue-i18n'

import AnswerCard from '@/components/answer/AnswerCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'

withDefaults(
  defineProps<{
    /** 这道题；还没读回来是 `null`，那颗按钮就换成骨架屏。 */
    questionData?: Question | null
    /** 这一份回答；还没读回来是 `null`。 */
    answerData?: Answer | null
    /** 只有提问的人能采纳。 */
    canAccept: boolean
    /** 全局默认头像的 id；`null` 是还没问到。 */
    defaultAvatarId?: number | null
  }>(),
  {
    questionData: null,
    answerData: null,
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

const { t } = useI18n()
</script>

<template>
  <div>
    <BaseButton
      v-if="questionData"
      exact
      block
      kind="secondary"
      class="mb-4"
      :to="{ name: 'QuestionAnswerList', params: { questionId: questionData.id.toString() } }"
    >
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
      :default-avatar-id="defaultAvatarId"
      @accept="emit('accept', $event)"
      @upvote="emit('upvote', $event)"
      @downvote="emit('downvote', $event)"
      @cancel-vote="emit('cancel-vote', $event)"
      @favorite="emit('favorite', $event)"
    />
    <v-skeleton-loader v-else type="list-item-avatar, paragraph, button@2" />
  </div>
</template>
