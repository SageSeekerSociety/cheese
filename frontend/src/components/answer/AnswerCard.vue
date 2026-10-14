<!-- eslint-disable vue/no-v-html -->
<template>
  <v-card :id="`answer-${answer.id}`" flat rounded="lg">
    <v-card-item>
      <v-card-title>{{ answer.author.nickname }}</v-card-title>
      <v-card-subtitle data-user-content>{{ answer.author.intro }}</v-card-subtitle>
      <template #prepend>
        <user-avatar :avatar="avatarUrl" :name="answer.author.nickname" :seed="answer.author.username" />
      </template>
    </v-card-item>
    <v-card-text class="text-body-1 font-weight-regular answer-body-text pb-1 px-3">
      <collapsible-content :max-height="200">
        <div class="rich-content" v-html="contentHtml"></div>
      </collapsible-content>
      <div v-if="canAccept" class="mt-4">
        <BaseButton
          v-if="!question?.accepted_answer"
          kind="primary"
          prepend-icon="mdi-check"
          @click="emit('accept', answer)"
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
        @upvote="emit('upvote', answer)"
        @downvote="emit('downvote', answer)"
        @cancel-vote="emit('cancel-vote', answer)"
      />
      <BaseButton kind="ghost">
        <v-icon size="18" class="me-2">mdi-comment-outline</v-icon>
        {{ t('questions.detail.buttons.comment') }}
        <span v-if="answer.comment_count">{{ answer.comment_count }}</span>
      </BaseButton>
      <BaseButton kind="ghost" @click="emit('favorite', answer)">
        <v-icon size="18" class="me-2">mdi-star-outline</v-icon>
        {{ answer.is_favorite ? t('questions.detail.buttons.unfavorite') : t('questions.detail.buttons.favorite') }}
      </BaseButton>
    </v-card-actions>
  </v-card>
</template>

<script setup lang="ts">
// 答案卡片的画面：作者、正文、采纳/投票/收藏那几颗按钮。这几件事都要发请求，落在页面
// 那边的 `useAnswerActions` 里；这里只画收到的状态、把要点的事交上去（连同「这个人能
// 不能采纳」那个判断，判据在那边）。头像是不是他自己挑的也由外面递进来：判据要读一个
// 取回来的全局默认头像 id，那也是取数。
import type { Answer, Question } from '@/types'

import { computed, toRefs } from 'vue'
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
    /** 只有提问的人能采纳。 */
    canAccept: boolean
    /** 全局默认头像的 id；`null` 是还没问到，这时一律当「他自己挑的」。 */
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

const { answer, question } = toRefs(props)

const contentHtml = computed(() => parse(JSON.parse(answer.value.content)))
const currentVote = computed(() => {
  switch (answer.value.attitudes.user_attitude) {
    case NewAttitudeType.Positive:
      return 'up'
    case NewAttitudeType.Negative:
      return 'down'
    default:
      return null
  }
})

/** 挑过的头像才画；没挑的（或者用的就是全局默认那张）走名字首字母。 */
const avatarUrl = computed(() => {
  const avatarId = answer.value.author.avatarId
  if (avatarId == null || avatarId === props.defaultAvatarId) return ''
  return getAvatarUrl(avatarId)
})
</script>
