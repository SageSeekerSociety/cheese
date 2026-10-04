<script setup lang="ts">
/**
 * 看板上「本视图内按标题找」的那个框（同 Linear 的 find in view）。只管输入：筛哪些活
 * 由看板自己做。`/` 聚焦（焦点不在输入框里时），Esc 清空。
 */
import { onMounted, onUnmounted, ref } from 'vue'

import { t } from '@/i18n'

const text = defineModel<string>({ required: true })
const input = ref<HTMLInputElement | null>(null)

function onKey(event: KeyboardEvent) {
  if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return
  const el = event.target as HTMLElement | null
  if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable))
    return
  event.preventDefault()
  input.value?.focus()
}
// 有字时 Esc 只清字，并把这一下吃掉：窄窗口里侧栏浮层也听 Esc（useEscapeStack），
// 不吃掉的话清字的同时把侧栏也收了。
function clear(event: KeyboardEvent) {
  if (!text.value) return
  event.preventDefault()
  text.value = ''
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <label class="board-find t-meta">
    <v-icon size="15" aria-hidden="true">mdi-magnify</v-icon>
    <input
      ref="input"
      v-model="text"
      type="search"
      autocomplete="off"
      :placeholder="t('work.board.findPlaceholder')"
      :aria-label="t('work.board.findLabel')"
      @keydown.esc="clear"
    />
  </label>
</template>

<style scoped>
.board-find {
  display: flex;
  flex: 0 1 220px;
  gap: 6px;
  align-items: center;
  min-width: 120px;
  margin-left: auto;
  padding: 4px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  color: var(--muted);
}

.board-find:focus-within {
  border-color: var(--focus-ring);
}

.board-find input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  outline: none;
  background: none;
  color: var(--ink);
  font: inherit;
}
</style>
