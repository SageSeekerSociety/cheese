<script setup lang="ts">
// 这条消息还带着什么：回复的那条在最前，后面是待发的附件。
//
// 从 `RoomComposer.vue` 里拆出来的（#2143）：一行排开、不换行，多了就在这一行里
// 横着滚，每一个都还拿得掉。它只吃 props、只往上发事件（拿掉哪一个由房间决定）。
import type { ChatAttachment } from '../../cx_types'

import AttachmentChip from './AttachmentChip.vue'
import ComposerChip from './ComposerChip.vue'

import { t } from '@/i18n'

defineProps<{
  /** 这条消息回复的是哪一条，读出来的样子（「回复 谁：说了什么」）。不回复时是 null。 */
  replyLabel?: string | null
  /** 已经传上去、等着跟下一条一起发出去的附件。 */
  atts: ChatAttachment[]
  /** 附件缩略图要从哪个话题的工作区取字节。 */
  topicId: string | null
}>()

const emit = defineEmits<{
  (e: 'remove-att', index: number): void
  (e: 'retry-att', index: number): void
  (e: 'clear-reply'): void
}>()

// 拿掉的那一枚离开时脱出排版（absolute），后面的才能滑过来补位；脱出之前先把它钉
// 在原来的位置上，不然它会跳到这一行的最左边再淡出。
function pinLeaving(el: Element) {
  const chip = el as HTMLElement
  chip.style.left = `${chip.offsetLeft}px`
  chip.style.top = `${chip.offsetTop}px`
  chip.style.width = `${chip.offsetWidth}px`
}
</script>

<template>
  <!-- 这一行长出来、收回去都是高度过渡；里面的标签一个个弹进来，拿掉一个时
       后面的滑过来补位。 -->
  <Transition name="chip-row">
    <div v-if="replyLabel || atts.length" class="chip-row">
      <TransitionGroup tag="div" name="chip" class="chip-list" @before-leave="pinLeaving">
        <ComposerChip
          v-if="replyLabel"
          key="reply"
          class="reply-chip"
          quiet
          :label="replyLabel"
          :remove-label="t('work.room.composer.cancelReply')"
          @remove="emit('clear-reply')"
        >
          <template #face><v-icon size="13">mdi-reply</v-icon></template>
        </ComposerChip>
        <AttachmentChip
          v-for="(a, i) in atts"
          :key="a.path"
          :topic-id="topicId"
          :attachment="a"
          @remove="emit('remove-att', i)"
          @retry="emit('retry-att', i)"
        />
      </TransitionGroup>
    </div>
  </Transition>
</template>

<style scoped>
.chip-row {
  display: grid;
  grid-template-rows: 1fr;
}
.chip-row-enter-active {
  transition:
    grid-template-rows var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.chip-row-leave-active {
  transition:
    grid-template-rows var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.chip-row-enter-from,
.chip-row-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}
/* 行高收到 0 时，里面那一行自己的上下内边距也得跟着收，不然最后剩 6px 再一跳。 */
.chip-row-enter-active .chip-list {
  transition: padding var(--dur-base) var(--ease-out);
}
.chip-row-leave-active .chip-list {
  transition: padding var(--dur-quick) var(--ease-in);
}
.chip-row-enter-from .chip-list,
.chip-row-leave-to .chip-list {
  padding-block: 0;
}
.chip-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.chip-leave-active {
  position: absolute;
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.chip-enter-from,
.chip-leave-to {
  opacity: 0;
  transform: scale(0.9);
}
.chip-move {
  transition: transform var(--dur-base) var(--ease-standard);
}
/* 回复和附件那一行。不换行：换了行输入框就被一截一截往上顶。多出来的横着滚——
   裁掉的话，第四个附件既看不见也拿不掉。 */
.chip-list {
  position: relative;
  min-height: 0;
  display: flex;
  gap: 6px;
  padding: 2px 0 4px;
  overflow-x: auto;
  scrollbar-width: thin;
}
/* 触屏上手指点得中：回复、附件那一行横着滚，滚动的盒子会裁掉 ✕ 撑出去的范围，
   上下各留 8px。 */
@media (pointer: coarse) {
  .chip-list {
    padding-block: 8px;
  }
}
</style>
