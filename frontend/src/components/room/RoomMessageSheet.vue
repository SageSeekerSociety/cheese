<script setup lang="ts">
// 触屏上一条消息的操作：长按消息，从底部升起这块面板。它和桌面的悬停条是同一组操作
// （表情、回复、编辑、复制、转为话题），只是换了一种摆法：表情排成顶上一行，其余
// 一行一项。触屏上没有悬停，悬停条不出现，所以悬停条上有的这里一样都不能少。
import type { Block } from '../../cx_types'
import type { MenuAction } from '../common/menuAction'

import { computed } from 'vue'
import { toast } from 'vuetify-sonner'

import MobileActionSheet from '../common/MobileActionSheet.vue'

import { copyMessage, QUICK_EMOJIS } from './messageActions'

import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })

const props = defineProps<{
  /** 长按的那一条。面板收起时还留着，收起的那一下里内容不会先没了。 */
  block: Block | null
  isAgent: boolean
  /** 这条是我自己发的消息：多一项「编辑」。 */
  editable: boolean
}>()

const emit = defineEmits<{
  (e: 'react', block: Block, emoji: string): void
  (e: 'reply', block: Block): void
  (e: 'upgrade', blockId: string): void
  (e: 'edit', block: Block): void
}>()

async function copy(block: Block) {
  if (await copyMessage(block, props.isAgent)) toast(t('work.room.message.copied'))
}

const actions = computed<MenuAction[]>(() => {
  const block = props.block
  if (!block) return []
  const list: MenuAction[] = [
    {
      key: 'reply',
      label: t('work.room.message.reply'),
      icon: 'mdi-reply-outline',
      onSelect: () => emit('reply', block),
    },
    { key: 'copy', label: t('work.room.message.copy'), icon: 'mdi-content-copy', onSelect: () => void copy(block) },
  ]
  if (props.editable) {
    list.push({
      key: 'edit',
      label: t('work.room.message.edit'),
      icon: 'mdi-pencil-outline',
      onSelect: () => emit('edit', block),
    })
  }
  list.push({
    key: 'upgrade',
    label: t('work.room.message.upgrade'),
    icon: 'mdi-comment-arrow-right-outline',
    onSelect: () => emit('upgrade', block.id),
  })
  return list
})

function react(emoji: string) {
  if (!props.block) return
  open.value = false
  emit('react', props.block, emoji)
}
</script>

<template>
  <MobileActionSheet v-model="open" :actions="actions">
    <template #header>
      <div class="msg-sheet__emojis" role="group" :aria-label="t('work.room.message.react')">
        <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="msg-sheet__emoji" @click="react(e)">
          {{ e }}
        </button>
      </div>
    </template>
  </MobileActionSheet>
</template>

<style scoped>
/* 八个表情一行排开，每一个都是一整格能点的范围。360px 宽的屏上一格略小于 44px，
   换行的话第二行只剩一两个，读起来像是漏了什么。 */
.msg-sheet__emojis {
  display: flex;
  margin: 0 -8px;
}
.msg-sheet__emoji {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  border: none;
  border-radius: var(--radius-md);
  background: none;
  /* 量的是一枚 emoji 字形，不是正文，所以不走字号阶梯；`line-height: 1` 是把字形
     在格子里居中的手段（同悬停条的 .rx-pick）。 */
  font-size: 23px;
  line-height: 1;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.msg-sheet__emoji:active {
  background: var(--fill);
}
</style>
