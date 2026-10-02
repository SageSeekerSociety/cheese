<script setup lang="ts">
// 消息的悬停条：整列只有这一个，指针落在哪条消息上，它就滑到哪条的右上角。
//
// 原来每条消息各带一个，指针换一行就是旧的淡出、新的淡入，两处同时在动，而且看
// 不出它们是同一个东西。只留一个之后，它在行与行之间滑过去，只有移出整列时才淡
// 出（设计系统 §9.7：一次只有一个焦点在动）。表情选择条挂在它下面，一起走。
//
// 它不认识时间线：停在哪条消息上、离顶多远，都是房间算好传进来的。
import type { Block } from '../../cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { copyMessage, QUICK_EMOJIS } from './messageActions'

import { t } from '@/i18n'

const props = defineProps<{
  /** 停在哪条消息上。收起时还留着上一条，淡出的那一下里按钮不会先没了。 */
  block: Block | null
  shown: boolean
  /** 那条消息的顶边离时间线内容顶部多少 px。 */
  top: number
  /** 从收起状态出现：这一下直接落到位，不从上一次的位置滑过来。 */
  jump: boolean
  isAgent: boolean
  pickerOpen: boolean
  /** 这条是我自己发的消息：多一颗「编辑」。 */
  editable: boolean
}>()

const emit = defineEmits<{
  (e: 'react', block: Block, emoji: string): void
  (e: 'toggle-picker', blockId: string): void
  (e: 'reply', block: Block): void
  (e: 'upgrade', blockId: string): void
  (e: 'edit', block: Block): void
}>()

// 复制之后原地说一声「已复制」，一会儿再换回来；换了一条消息就不再说。
const COPIED_MS = 1500
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => props.block?.id,
  () => {
    copied.value = false
    clearTimeout(copiedTimer)
  }
)
onBeforeUnmount(() => clearTimeout(copiedTimer))

async function copy() {
  if (!props.block || !(await copyMessage(props.block, props.isAgent))) return
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), COPIED_MS)
}
</script>

<template>
  <div
    class="hover-bar"
    :class="{ 'hover-bar--shown': shown, 'hover-bar--jump': jump }"
    :style="{ transform: `translateY(${top}px)` }"
    :aria-hidden="!shown"
  >
    <template v-if="block">
      <button
        type="button"
        class="hover-bar__act rx-toggle"
        :class="{ 'hover-bar__act--on': pickerOpen }"
        :title="t('work.room.message.react')"
        @click="emit('toggle-picker', block.id)"
      >
        <v-icon size="15">mdi-emoticon-happy-outline</v-icon>
      </button>
      <button type="button" class="hover-bar__act" :title="t('work.room.message.reply')" @click="emit('reply', block)">
        <v-icon size="15">mdi-reply-outline</v-icon>
      </button>
      <button
        v-if="editable"
        type="button"
        class="hover-bar__act"
        :title="t('work.room.message.edit')"
        @click="emit('edit', block)"
      >
        <v-icon size="15">mdi-pencil-outline</v-icon>
      </button>
      <button
        type="button"
        class="hover-bar__act"
        :title="copied ? t('work.room.message.copied') : t('work.room.message.copy')"
        @click="copy"
      >
        <v-icon size="15">{{ copied ? 'mdi-check' : 'mdi-content-copy' }}</v-icon>
      </button>
      <button
        type="button"
        class="hover-bar__act"
        :title="t('work.room.message.upgrade')"
        @click="emit('upgrade', block.id)"
      >
        <v-icon size="15">mdi-comment-arrow-right-outline</v-icon>
      </button>
      <!-- MVP emoji picker: the 8 common reactions, Slack-style. 从那颗按钮下面长
           出来，收回也回到那里。 -->
      <Transition name="rx-picker">
        <div v-if="pickerOpen" class="rx-picker">
          <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="rx-pick" @click="emit('react', block, e)">
            {{ e }}
          </button>
        </div>
      </Transition>
    </template>
  </div>
</template>

<style scoped>
/* 整条放在消息顶边之上；续话没有名字行，探入行内就会挡住正文里的链接。
   换一行时它滑过去（transform），出现和收起只改透明度。 */
.hover-bar {
  position: absolute;
  top: 0;
  right: 16px;
  z-index: 4;
  display: flex;
  gap: 2px;
  padding: 3px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  translate: 0 -100%;
  opacity: 0;
  pointer-events: none;
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-base) var(--ease-standard);
}
.hover-bar--shown {
  opacity: 1;
  pointer-events: auto;
  transition:
    opacity var(--dur-quick) var(--ease-out),
    transform var(--dur-base) var(--ease-standard);
}
/* 从收起状态出现时直接落到位，只淡入：从上一次停的那一行滑过来，说的是一件没
   发生的事。 */
.hover-bar--jump {
  transition: opacity var(--dur-quick) var(--ease-out);
}
/* One quiet square button per action: muted ink, fill on hover. */
.hover-bar__act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.hover-bar__act:hover {
  background: var(--fill);
  color: var(--ink);
}
.hover-bar__act--on {
  background: var(--line-2);
  color: var(--ink);
}
.rx-picker-enter-active {
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.rx-picker-leave-active {
  transition:
    transform var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.rx-picker-enter-from,
.rx-picker-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
.rx-picker {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 5;
  display: flex;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  transform-origin: top right;
}
.rx-pick {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  /* 这个 16px 量的是一枚 emoji 字形，不是正文，所以不走字号阶梯；`line-height: 1`
     同理——它是把字形在 28px 方格里居中的手段，不是一段话的行距。 */
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.rx-pick:hover {
  background: var(--fill);
}
</style>
