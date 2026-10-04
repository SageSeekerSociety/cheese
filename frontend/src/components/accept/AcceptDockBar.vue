<script setup lang="ts">
// 贴在输入框上方的一条：平时只有一行（这是什么、等谁、去验收），点开才在它上面
// 展开整张卡。它原来是对话末尾一张 280px 高的卡，一递上来对话就只剩几行；而它
// 说的是「有一个决定在等人」，这件事一行就说得完。
//
// 这一条只画，不知道卡里是什么：一行上的四个字（图标、颜色、标题、等谁）由调用方
// 从卡上算好递进来，点的两下报回去。展开的收放也是调用方的事 —— 展开的那一块长在
// 这一条的上面，不在它里面。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  icon: string
  color: string
  title: string
  sub: string
  expanded: boolean
  /** 有没有东西可以「去验收」（待采纳的卡才有）。 */
  canReview: boolean
}>()

defineEmits<{
  (e: 'toggle'): void
  (e: 'review'): void
}>()
</script>

<template>
  <div class="accept-bar">
    <button
      type="button"
      class="accept-bar__toggle"
      :aria-expanded="expanded"
      aria-controls="accept-detail"
      :title="expanded ? t('work.room.accept.collapse') : t('work.room.accept.expand')"
      @click="$emit('toggle')"
    >
      <v-icon :color="color" size="18">{{ icon }}</v-icon>
      <span class="accept-bar__title">{{ title }}</span>
      <span v-if="sub" class="accept-bar__sub">{{ sub }}</span>
      <v-icon size="16" class="accept-bar__caret">{{ expanded ? 'mdi-chevron-down' : 'mdi-chevron-up' }}</v-icon>
    </button>
    <!-- 决策在聊天，审查在面板: the bar asks for a decision, and the thing the
         decision is about is a diff in the panel next to it. -->
    <BaseButton v-if="canReview" kind="primary" size="sm" @click="$emit('review')">
      {{ t('work.room.accept.review') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.accept-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 4px 8px 4px 4px;
}
.accept-bar__toggle {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.accept-bar__toggle:hover {
  background: var(--fill);
}
/* 标题可以是一次改动的整句标题（最长 72 字），窄屏上一行放不下，所以它也跟着截断；
   「等谁」那半句更短，先让标题让位。 */
.accept-bar__title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
.accept-bar__sub {
  flex: 0 0 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.accept-bar__caret {
  flex: none;
  margin-left: auto;
  color: var(--faint);
}
</style>
