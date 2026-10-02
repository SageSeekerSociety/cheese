<script setup lang="ts">
// 选中文字后浮在上面的那一条：AI 队友、评论，能改时再加上块样式和几种字的格式、链接；
// 不能改时把格式换成「复制」。
//
// 位置由 DocOverlays 算好递进来；这里只画，以及就地改格式。改格式的那一笔带着
// BUBBLE_META（lib/docBubble.ts）：浮条看到正文变了本该收起（光标底下的字换了），而这一笔是它自己改的，
// 选区还是那一段，浮条应当留着。
import type { ChainedCommands, Editor } from '@tiptap/core'

import { computed, onBeforeUnmount, ref, toRaw, watch } from 'vue'

import { BUBBLE_META } from '../../../lib/docBubble'
import { SLASH_ITEMS } from '../../../lib/docSlashMenu'
import CheeseAvatar from '../../CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{
  editor: Editor
  agentName: string
  agentHandle?: string | null
  /** 能改：给格式和链接；不能改：给「复制」。 */
  editable: boolean
  /** 选区在一段之内：给「正文 ▾」。 */
  singleBlock: boolean
  /** 浮条上给不给 AI 队友。 */
  canAgent: boolean
}>()
const emit = defineEmits<{
  (e: 'agent'): void
  (e: 'comment'): void
  (e: 'link'): void
  (e: 'copy'): void
}>()

// 正文每变一次重算哪几个格式亮着。
const revision = ref(0)
watch(
  () => toRaw(props.editor),
  (editor, _previous, cleanup) => {
    const update = () => revision.value++
    editor.on('transaction', update)
    cleanup(() => editor.off('transaction', update))
  },
  { immediate: true }
)

const MARKS = [
  { key: 'bold', icon: 'mdi-format-bold', mark: 'bold' },
  { key: 'italic', icon: 'mdi-format-italic', mark: 'italic' },
  { key: 'strike', icon: 'mdi-format-strikethrough', mark: 'strike' },
  { key: 'code', icon: 'mdi-code-tags', mark: 'code' },
  { key: 'highlight', icon: 'mdi-marker', mark: 'highlight' },
] as const

const marks = computed(() => {
  void revision.value
  const editor = toRaw(props.editor)
  return MARKS.map((item) => ({
    ...item,
    active: editor.isActive(item.mark),
    disabled: !editor.can().toggleMark(item.mark),
  }))
})

function format(run: (chain: ChainedCommands) => ChainedCommands) {
  const editor = toRaw(props.editor)
  run(editor.chain().focus(undefined, { scrollIntoView: false }).setMeta(BUBBLE_META, true)).run()
}

// ---- 「正文 ▾」：把选区所在的这一块换成别的块。和 slash 菜单是同一张表，去掉插入新
// 东西的那几项（表格、分隔线）。
const BLOCKS = SLASH_ITEMS.filter((item) => item.key !== 'table' && item.key !== 'hr')
const blockOpen = ref(false)
const currentBlock = computed(() => {
  void revision.value
  const editor = toRaw(props.editor)
  const found = [...BLOCKS].reverse().find((item) => {
    switch (item.key) {
      case 'h1':
      case 'h2':
      case 'h3':
        return editor.isActive('heading', { level: Number(item.key.slice(1)) })
      case 'bullet':
        return editor.isActive('bulletList')
      case 'ordered':
        return editor.isActive('orderedList')
      case 'task':
        return editor.isActive('taskList')
      case 'code':
        return editor.isActive('codeBlock')
      case 'quote':
        return editor.isActive('blockquote')
      default:
        return false
    }
  })
  return found ?? BLOCKS[0]
})
function pickBlock(run: (chain: ChainedCommands) => ChainedCommands) {
  blockOpen.value = false
  format(run)
}
function closeBlocks(e: MouseEvent) {
  if (!(e.target as HTMLElement | null)?.closest('.doc-bubble__blocks')) blockOpen.value = false
}
watch(blockOpen, (open) => {
  if (open) document.addEventListener('mousedown', closeBlocks, true)
  else document.removeEventListener('mousedown', closeBlocks, true)
})
onBeforeUnmount(() => document.removeEventListener('mousedown', closeBlocks, true))
</script>

<template>
  <div class="doc-bubble" role="toolbar" :aria-label="t('work.room.doc.selectionToolbar')" @mousedown.prevent>
    <button v-if="canAgent" type="button" class="doc-bubble__agent" :aria-label="agentName" @click="emit('agent')">
      <CheeseAvatar :size="16" :name="agentName" :handle="agentHandle" />
      {{ agentName }}
    </button>
    <span v-if="canAgent" class="doc-bubble__sep" aria-hidden="true" />
    <button type="button" :aria-label="t('work.room.doc.commentOnSelection')" @click="emit('comment')">
      {{ t('work.room.comments.comment') }}
    </button>
    <template v-if="editable">
      <span class="doc-bubble__sep" aria-hidden="true" />
      <div v-if="singleBlock" class="doc-bubble__blocks">
        <button
          type="button"
          :aria-label="t('work.room.doc.blockType')"
          aria-haspopup="menu"
          :aria-expanded="blockOpen"
          @click="blockOpen = !blockOpen"
        >
          {{ currentBlock.label }}
          <v-icon size="14">mdi-chevron-down</v-icon>
        </button>
        <div v-if="blockOpen" class="doc-bubble__menu" role="menu">
          <button
            v-for="item in BLOCKS"
            :key="item.key"
            type="button"
            role="menuitemradio"
            :aria-checked="item.key === currentBlock.key"
            class="doc-bubble__item"
            @click="pickBlock(item.run)"
          >
            <v-icon size="16">{{ item.icon }}</v-icon>
            {{ item.label }}
          </button>
        </div>
      </div>
      <button
        v-for="item in marks"
        :key="item.key"
        type="button"
        class="doc-bubble__icon"
        :aria-label="t(`work.room.doc.format.${item.key}`)"
        :title="t(`work.room.doc.format.${item.key}`)"
        :aria-pressed="item.active"
        :disabled="item.disabled"
        @click="format((c) => c.toggleMark(item.mark))"
      >
        <v-icon size="17">{{ item.icon }}</v-icon>
      </button>
      <button
        type="button"
        class="doc-bubble__icon"
        :aria-label="t('work.room.docLink.title')"
        :title="t('work.room.docLink.title')"
        @click="emit('link')"
      >
        <v-icon size="17">mdi-link-variant</v-icon>
      </button>
    </template>
    <button v-else type="button" @click="emit('copy')">{{ t('work.room.doc.copy') }}</button>
  </div>
</template>

<style scoped>
/* 一块深色的条，压在正文上（两套主题都用反色 token）。 */
.doc-bubble {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 3px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--inverse-ink);
  background: var(--inverse-surface);
  box-shadow: var(--shadow-2);
  white-space: nowrap;
}
.doc-bubble button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  border: none;
  background: transparent;
  color: inherit;
  padding: 0 9px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-bubble button:hover:not(:disabled),
.doc-bubble button[aria-pressed='true'],
.doc-bubble button[aria-expanded='true'] {
  background: var(--inverse-fill);
}
.doc-bubble button:disabled {
  opacity: 0.4;
  cursor: default;
}
.doc-bubble button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.doc-bubble .doc-bubble__icon {
  width: 30px;
  padding: 0;
  justify-content: center;
}
.doc-bubble__agent {
  font-weight: 600;
}
.doc-bubble__sep {
  width: 1px;
  height: 16px;
  margin: 0 3px;
  background: var(--inverse-fill);
}
.doc-bubble__blocks {
  position: relative;
}
/* 块样式的菜单是浅色的一张，和 slash 菜单一个样子。 */
.doc-bubble__menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  display: flex;
  flex-direction: column;
  min-width: 168px;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.doc-bubble .doc-bubble__item {
  justify-content: flex-start;
  gap: 10px;
  height: 32px;
  color: var(--ink);
  font-size: 13px;
}
.doc-bubble .doc-bubble__item:hover,
.doc-bubble .doc-bubble__item[aria-checked='true'] {
  background: var(--fill);
}
.doc-bubble__item .v-icon {
  color: var(--muted);
}
</style>
