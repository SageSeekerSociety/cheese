<script setup lang="ts">
// 章节刻度：这一节从这条消息开始。它和日期线、未读线一样横跨整列，但它说的不是时间，
// 是内容的结构，所以它不走 TimelineMark 那三档（见那个组件的说明）。
import { t } from '@/i18n'

defineOptions({ name: 'ChapterMark' })

defineProps<{
  /** 第几章，从 1 数起，按对话里的顺序。 */
  no: number
  /** 这一章叫什么：起头那条消息的第一行。 */
  title: string
}>()
</script>

<template>
  <div class="chapter-mark" role="heading" aria-level="3">
    <span class="chapter-mark__rule" aria-hidden="true" />
    <span class="chapter-mark__body">
      <v-icon size="12" class="chapter-mark__icon">mdi-bookmark</v-icon>
      <span class="chapter-mark__no">{{ t('work.room.chapter.mark', { no }) }}</span>
      <span class="chapter-mark__sep" aria-hidden="true">·</span>
      <span class="chapter-mark__title">{{ title }}</span>
    </span>
    <span class="chapter-mark__rule" aria-hidden="true" />
  </div>
</template>

<style scoped>
.chapter-mark {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 20px 16px 8px;
}
.chapter-mark__rule {
  flex: 1 1 auto;
  height: 1px;
  background: var(--line);
}
.chapter-mark__body {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
}
.chapter-mark__icon {
  flex: none;
  color: var(--faint);
}
.chapter-mark__no {
  flex: none;
  color: var(--muted);
}
.chapter-mark__sep {
  flex: none;
  color: var(--faint);
}
.chapter-mark__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--ink);
}
</style>
