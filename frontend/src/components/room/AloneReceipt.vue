<script setup lang="ts">
// 我发的那条下面的一行回执：这条没交给 AI 队友，而这里只有我一个人。
//
// 只有这两件事同时成立时才有（后端回这条消息时说的 `alone`，不落库、只给发的人）。
// 多人的频道里没有它：频道默认是人和人说话，一条没交给芝士的消息再正常不过，每条都
// 提醒一句就成了催人（#1743 删掉的就是那种提示）。只有自己一个人时，什么都不发生和
// 「芝士没理我」看起来一模一样，这一行把两者分开，并给出本来就有的那条路：回复这条、
// @ 它。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** 项目给 AI 队友起的名字。 */
  agentName: string
}>()

const emit = defineEmits<{
  /** 回复这一条，并在正文前面 @ 上队友。 */
  (e: 'ask'): void
}>()
</script>

<template>
  <div class="alone-receipt" role="status">
    <span class="alone-receipt__text">{{ t('work.room.composer.recipient.alone', { name: agentName }) }}</span>
    <BaseButton kind="ghost" size="sm" @click="emit('ask')">
      {{ t('work.room.composer.recipient.aloneAsk', { name: agentName }) }}
    </BaseButton>
  </div>
</template>

<style scoped>
/* 对齐消息正文：左边让出头像那一栏（28px）和它与正文之间的那道缝。 */
.alone-receipt {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  padding: 2px 0 8px 40px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.alone-receipt__text {
  min-width: 0;
}
</style>
