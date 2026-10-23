<script setup lang="ts">
// 看着的那一处建议下面的卡：谁提的、第几处，接受或拒绝。
//
// 提建议的人可能是人，也可能是 AI 队友：人画自己的头像，AI 队友画芝士头像，
// 名字都是建议自己的作者，不是项目里默认那一位。
import CheeseAvatar from '../../CheeseAvatar.vue'
import UserAvatar from '../../common/UserAvatar.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{
  /** 提建议的人的名字（自己是「你」）。 */
  authorName: string
  /** 提建议的人的 handle：人像和芝士头像的底色都跟着它。 */
  authorHandle: string | null
  /** 提建议的是不是 AI 队友：是就画芝士头像，不是就画人的头像。 */
  authorAgent: boolean
  /** 人挑过的头像地址；AI 队友不用，给空串。 */
  authorAvatar: string
  index: number
  total: number
  /** 它说的理由；没有就不写。 */
  reason: string | null
  editable: boolean
}>()
const emit = defineEmits<{
  (e: 'accept'): void
  (e: 'reject'): void
}>()
</script>

<template>
  <div class="doc-suggestion-card" role="group" :aria-label="t('work.room.docSuggest.title', { author: authorName })">
    <CheeseAvatar
      v-if="authorAgent"
      :size="22"
      :name="authorName"
      :handle="authorHandle"
      class="doc-suggestion-card__avatar"
    />
    <UserAvatar
      v-else
      :size="22"
      :avatar="authorAvatar"
      :name="authorName"
      :seed="authorHandle ?? ''"
      class="doc-suggestion-card__avatar"
    />
    <div class="doc-suggestion-card__body">
      <span class="doc-suggestion-card__title">{{ t('work.room.docSuggest.title', { author: authorName }) }}</span>
      <span class="doc-suggestion-card__count">
        · {{ t('work.room.docSuggest.position', { i: index + 1, n: total }) }}</span
      >
      <p v-if="reason" class="doc-suggestion-card__reason">{{ reason }}</p>
    </div>
    <template v-if="editable">
      <DocEditButton @click="emit('reject')">{{ t('work.room.docSuggest.reject') }}</DocEditButton>
      <DocEditButton strong @click="emit('accept')">{{ t('work.room.docSuggest.accept') }}</DocEditButton>
    </template>
  </div>
</template>

<style scoped>
.doc-suggestion-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-suggestion-card__avatar {
  flex: 0 0 auto;
  margin-top: 1px;
}
.doc-suggestion-card__body {
  flex: 1 1 160px;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-suggestion-card__title {
  color: var(--ink);
  font-weight: 600;
}
.doc-suggestion-card__count {
  color: var(--faint);
}
.doc-suggestion-card__reason {
  margin: 2px 0 0;
  color: var(--text);
}
</style>
