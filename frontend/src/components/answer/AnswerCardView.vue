<!-- eslint-disable vue/no-v-html -->
<template>
  <v-card :id="`answer-${answer.id}`" flat rounded="lg">
    <v-card-item>
      <v-card-title>{{ answer.author.nickname }}</v-card-title>
      <v-card-subtitle data-user-content>{{ answer.author.intro }}</v-card-subtitle>
      <template #prepend>
        <user-avatar :avatar="getAvatarUrl(answer.author.avatarId)" />
      </template>
    </v-card-item>
    <v-card-text class="text-body-1 font-weight-regular answer-body-text pb-1 px-3">
      <collapsible-content :max-height="200">
        <div class="rich-content" v-html="contentHtml"></div>
      </collapsible-content>
      <div v-if="canAccept" class="mt-4">
        <BaseButton
          v-if="question && !question.accepted_answer"
          kind="primary"
          prepend-icon="mdi-check"
          @click="$emit('accept')"
        >
          {{ t('questions.detail.buttons.accept') }}
        </BaseButton>
      </div>
    </v-card-text>
    <v-card-actions class="px-3">
      <content-voter
        :score="answer.attitudes.difference"
        :current-vote="currentVote"
        class="me-2"
        @upvote="$emit('upvote')"
        @downvote="$emit('downvote')"
        @cancel-vote="$emit('cancelVote')"
      />
      <BaseButton kind="ghost">
        <v-icon size="18" class="me-2">mdi-comment-outline</v-icon>
        {{ t('questions.detail.buttons.comment') }}
        <span v-if="answer.comment_count">{{ answer.comment_count }}</span>
      </BaseButton>
      <BaseButton kind="ghost" @click="$emit('favorite')">
        <v-icon size="18" class="me-2">mdi-star-outline</v-icon>
        {{ answer.is_favorite ? t('questions.detail.buttons.unfavorite') : t('questions.detail.buttons.favorite') }}
      </BaseButton>
    </v-card-actions>
  </v-card>
</template>

<script setup lang="ts">
// 一条回答**画的那一半**：只认 props、只往上发事件。
//
// 取数与那几个写操作（赞 / 踩 / 收藏 / 采纳 / `currentUserId` 判断）留在
// `AnswerCard.vue`，由它算好 `canAccept` 递进来；采纳回列表重刷也由它接走。这样
// 它在只装 Vuetify + i18n 的树里也能单独渲染。
import type { Answer, Question } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'
import { parse } from '@/utils/parser'

import CollapsibleContent from '../common/CollapsibleContent.vue'
import ContentVoter from '../common/ContentVoter.vue'
import UserAvatar from '../common/UserAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { NewAttitudeType } from '@/constants'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    answer: Answer
    question?: Question | null
    /** 当前登录者是不是这道题的题主（只有题主能给采纳按钮）。判断在 `AnswerCard.vue`。 */
    canAccept?: boolean
  }>(),
  {
    question: null,
    canAccept: false,
  }
)

defineEmits<{
  upvote: []
  downvote: []
  cancelVote: []
  favorite: []
  accept: []
}>()

const contentHtml = computed(() => parse(JSON.parse(props.answer.content)))
const currentVote = computed(() => {
  switch (props.answer.attitudes.user_attitude) {
    case NewAttitudeType.Positive:
      return 'up'
    case NewAttitudeType.Negative:
      return 'down'
    default:
      return null
  }
})
</script>
