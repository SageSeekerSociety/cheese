<script setup lang="ts">
// 文档内查找的那一条。逻辑（匹配、第几处、上下跳）在 composables/useDocFind.ts；
// 这里只画：一个输入框、一个「第几个 / 共几个」、上一个 / 下一个、关闭。
//
// 它浮在正文右上角，是面板里的一层，不占正文的宽度。打开靠顶栏那颗按钮（不劫持浏览
// 器的 Ctrl+F），键位只在打开之后管用：Enter 下一个、Shift+Enter 上一个、Esc 关闭。
import { nextTick, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  open: boolean
  query: string
  /** 共几处匹配。 */
  total: number
  /** 当前第几处（从 1 数）；没有匹配时是 0。 */
  current: number
}>()
const emit = defineEmits<{
  (e: 'update:query', value: string): void
  (e: 'next'): void
  (e: 'prev'): void
  (e: 'close'): void
}>()

const input = ref<HTMLInputElement | null>(null)

// 每次打开先把光标放进输入框，并全选上一次的查询 —— 接着敲就是替换，不用先删。
watch(
  () => props.open,
  async (open) => {
    if (!open) return
    await nextTick()
    input.value?.focus()
    input.value?.select()
  }
)

function onKeydown(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
    return
  }
  if (e.key === 'Enter') {
    e.preventDefault()
    if (e.shiftKey) emit('prev')
    else emit('next')
  }
}
</script>

<template>
  <Transition name="doc-menu">
    <div v-if="open" class="doc-find" role="search" :aria-label="t('work.room.doc.find')" @keydown="onKeydown">
      <v-icon size="18" class="doc-find__icon">mdi-magnify</v-icon>
      <input
        ref="input"
        class="doc-find__input"
        type="text"
        autocomplete="off"
        spellcheck="false"
        :value="query"
        :placeholder="t('work.room.doc.findPlaceholder')"
        :aria-label="t('work.room.doc.find')"
        @input="emit('update:query', ($event.target as HTMLInputElement).value)"
      />
      <span class="doc-find__count" aria-live="polite">
        <template v-if="query && total === 0">{{ t('work.room.doc.findNoResults') }}</template>
        <template v-else-if="total > 0">{{ current }}/{{ total }}</template>
      </span>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-chevron-up"
        :disabled="total === 0"
        :aria-label="t('work.room.doc.findPrev')"
        :title="t('work.room.doc.findPrev')"
        @click="emit('prev')"
      />
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-chevron-down"
        :disabled="total === 0"
        :aria-label="t('work.room.doc.findNext')"
        :title="t('work.room.doc.findNext')"
        @click="emit('next')"
      />
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        :aria-label="t('work.room.doc.findClose')"
        :title="t('work.room.doc.findClose')"
        @click="emit('close')"
      />
    </div>
  </Transition>
</template>

<style scoped>
/* 一行浮在正文上方、靠右的窄条 —— 和 DocSuggestionStrip / DocReviewStrip 一样是正文
   之上的一条，不与它们抢位置；不压住顶栏，也不改正文的宽度。 */
.doc-find {
  display: flex;
  flex: 0 0 auto;
  align-self: flex-end;
  align-items: center;
  gap: 4px;
  max-width: min(420px, calc(100% - 24px));
  margin: 8px 12px 0;
  padding: 3px 6px 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--fill);
}
.doc-find__icon {
  flex: 0 0 auto;
  color: var(--muted);
}
.doc-find__input {
  flex: 1 1 auto;
  min-width: 0;
  width: 180px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  outline: none;
}
.doc-find__input::placeholder {
  color: var(--faint);
}
.doc-find__count {
  flex: 0 0 auto;
  min-width: 44px;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  text-align: right;
  white-space: nowrap;
}
</style>
