<template>
  <v-sheet flat rounded="lg">
    <slot name="header"></slot>
    <!-- A failed load stays where the list would be: an empty list here is not "no answers yet". -->
    <BaseLoadError
      v-if="failed"
      :title="t('questions.answer.listLoadFailed')"
      :error="failureReason"
      :forbidden="forbidden"
      @retry="$emit('retry')"
    />
    <template v-else-if="refreshing">
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
      <v-skeleton-loader type="list-item-avatar, paragraph, button@2" />
    </template>
    <template v-else>
      <template v-if="answers.length === 0">
        <blank-page />
      </template>
      <template v-else>
        <answer-card-view
          v-for="answer in answers"
          :key="answer.id"
          :answer="answer"
          :question="question"
          :can-accept="canAccept"
          @upvote="$emit('upvote', answer)"
          @downvote="$emit('downvote', answer)"
          @cancel-vote="$emit('cancelVote', answer)"
          @favorite="$emit('favorite', answer)"
          @accept="$emit('accept', answer)"
        />
      </template>
    </template>
  </v-sheet>
</template>

<script lang="ts">
// 这一件的 props 单独立个名字并导出：`views/question/DetailAnswerListView.vue` 把它整
// 个转发一遍（那边没有自己的画面），两边共用这一份类型，就不用抄第二遍。类型导出不构成
// 依赖，那一件仍然是 A 级。
//
// 用到的 `Answer` / `Question` 从下面那个块的 import 来 —— 两个块编译后是同一个模块。
export interface AnswerListViewProps {
  answers: Answer[]
  question?: Question | null
  refreshing: boolean
  /** 当前登录者是不是题主：决定每张卡上画不画采纳按钮。判断在容器里。 */
  canAccept?: boolean
  /** 这次没读到：`answers` 空是假的。 */
  failed: boolean
  failureReason: string | null
  /** 401/403：不是「没读到」，是「不给你看」，不给重试。 */
  forbidden: boolean
}
</script>

<script setup lang="ts">
// 一道题下面那串回答**画的那一半**：只认 props、只往上发事件（每一颗的赞 / 踩 /
// 收藏 / 采纳都带着是哪一条回答）。
//
// 取数（`composables/useAnswerList`）与那几个写操作（`composables/useAnswerActions`）
// 留在 `AnswerList.vue` 和页面容器里；`canAccept` 也由它们算好递进来。
import type { Answer, Question } from '@/types'

import { useI18n } from 'vue-i18n'

import AnswerCardView from '@/components/answer/AnswerCardView.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BlankPage from '@/components/common/BlankPage.vue'

const { t } = useI18n()

withDefaults(defineProps<AnswerListViewProps>(), {
  question: null,
  canAccept: false,
})

defineEmits<{
  retry: []
  upvote: [answer: Answer]
  downvote: [answer: Answer]
  cancelVote: [answer: Answer]
  favorite: [answer: Answer]
  accept: [answer: Answer]
}>()
</script>
