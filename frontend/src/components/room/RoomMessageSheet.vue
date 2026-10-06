<script setup lang="ts">
// 触屏上一条消息的操作：长按消息，从底部升起这块面板。它和桌面的悬停条是同一组操作
// （表情、回复、编辑、复制、复制链接、转为话题），只是换了一种摆法：表情排成顶上一
// 行，其余一行一项。触屏上没有悬停，悬停条不出现，所以悬停条上有的这里一样都不能少。
//
// 长按已经拿来开这块面板，在消息上就没法再长按选字。要复制其中一段，走「选择文字」：
// 把这条消息单独放到一整页上，在那里按系统的方式选。
import type { Block } from '../../cx_types'
import type { MenuAction } from '../common/menuAction'

import { computed, ref } from 'vue'

import { useMessageLink } from '@/composables/useMessageLink'

import AdaptiveDialog from '../common/AdaptiveDialog.vue'
import MobileActionSheet from '../common/MobileActionSheet.vue'

import { copyMessage, QUICK_EMOJIS, shownMessageHtml } from './messageActions'

import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })

const props = defineProps<{
  /** 私聊里转出去的是一个新话题，别处是任务。 */
  upgradeToTopic?: boolean
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

// 复制成没成由共享的复制助子弹 toast，这里只管发起。
async function copy(block: Block) {
  await copyMessage(block, props.isAgent)
}

// 这条消息的站内链接（组件不碰路由，走 composable）。宿主没有路由时给不出链接，
// 这一项就不出现。复制成的说法同样由助手里那一条 toast 给。
const { hrefOf, copy: copyHref } = useMessageLink()
async function copyLink(block: Block) {
  await copyHref(block)
}

// 「选择文字」那一页：打开的那一刻照着屏幕上的这一条取一份，之后消息再变也不跟着
// 变，免得选到一半字换了。这一条不在屏幕上时退回原文。
const selecting = ref(false)
const selectHtml = ref<string | null>(null)
const selectText = ref('')

function openSelect(block: Block) {
  selectHtml.value = shownMessageHtml(block.id)
  selectText.value = block.content
  selecting.value = true
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
  if (hrefOf(block) !== null)
    list.push({
      key: 'link',
      label: t('work.room.message.copyLink'),
      icon: 'mdi-link-variant',
      onSelect: () => void copyLink(block),
    })
  list.push({
    key: 'select',
    label: t('work.room.message.selectText'),
    icon: 'mdi-format-text',
    onSelect: () => openSelect(block),
  })
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
    label: props.upgradeToTopic ? t('work.room.message.upgradeToTopic') : t('work.room.message.upgrade'),
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
  <AdaptiveDialog v-model="selecting" :title="t('work.room.message.selectText')">
    <!-- 取自页面上这条消息已经渲染、净化过的那一份。 -->
    <div
      v-if="selectHtml !== null"
      class="msg-select md-content"
      :class="{ 'msg-select--plain': !isAgent }"
      v-html="selectHtml"
    />
    <div v-else class="msg-select msg-select--plain">{{ selectText }}</div>
  </AdaptiveDialog>
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

/* 「选择文字」那一页。消息列表上为了长按关掉了选字和系统的长按菜单，这里要明着
   打开：这一页就是为了选字。 */
.msg-select {
  user-select: text;
  -webkit-user-select: text;
  -webkit-touch-callout: default;
  /* 比列表里大一档，手指拖选区时好对准。 */
  font-size: 15px;
  line-height: var(--lh-15-reading);
  color: var(--text);
  overflow-wrap: anywhere;
}
/* 人说的话照原样的换行和空格显示，和列表里一样。 */
.msg-select--plain {
  white-space: pre-wrap;
}
</style>
