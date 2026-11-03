<script setup lang="ts">
// 在页面上选中一段字，旁边出现「问芝士」：点了面板打开，这段字成为输入框上方的一枚标签，
// 跟着下一句交给芝士。桌面上在松开鼠标后出现在选区上方，手机上在选区下方（网页没法往
// 系统的选区菜单里加按钮，放上面会被它盖住）。
//
// 这几处不出：输入框和可编辑的地方（在那里选字是要改它），项目文档（那里有文档芝士的
// 选区框，`data-agent-select="off"`），芝士面板本身。团队文档没有文档芝士，编辑器上标着
// `data-agent-select="on"`，照常出。
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ enabled: boolean; touch: boolean }>()
const emit = defineEmits<{ ask: [text: string] }>()

const at = ref<{ top: number; left: number } | null>(null)
let text = ''
let settle: ReturnType<typeof setTimeout> | null = null

/** 选区落在哪一处：能不能在这里问芝士。 */
function allowed(node: Node | null): boolean {
  const el = node instanceof Element ? node : node?.parentElement
  if (!el) return false
  const marked = el.closest('[data-agent-select]')
  if (marked) return marked.getAttribute('data-agent-select') === 'on'
  return !el.closest('input, textarea, [contenteditable="true"], [contenteditable=""]')
}

function read() {
  const selection = window.getSelection()
  const picked = selection?.toString().trim() ?? ''
  if (!props.enabled || !selection || selection.isCollapsed || !picked || !selection.rangeCount) {
    at.value = null
    return
  }
  if (!allowed(selection.anchorNode) || !allowed(selection.focusNode)) {
    at.value = null
    return
  }
  const rect = selection.getRangeAt(0).getBoundingClientRect()
  if (!rect.width && !rect.height) {
    at.value = null
    return
  }
  text = picked
  const left = Math.min(Math.max(rect.left + rect.width / 2, 48), window.innerWidth - 48)
  at.value = props.touch ? { top: rect.bottom + 12, left } : { top: Math.max(rect.top - 40, 8), left }
}

/** 选完再出：拖着选的时候不跟着跳。触摸屏没有松开鼠标这一下，等选区停一会儿。 */
function later(ms: number) {
  if (settle) clearTimeout(settle)
  settle = setTimeout(read, ms)
}

const onUp = () => later(10)
const onChange = () => {
  if (window.getSelection()?.isCollapsed) at.value = null
  else if (props.touch) later(500)
}
const onScroll = () => (at.value = null)

function ask() {
  emit('ask', text)
  at.value = null
  window.getSelection()?.removeAllRanges()
}

onMounted(() => {
  document.addEventListener('mouseup', onUp)
  document.addEventListener('selectionchange', onChange)
  window.addEventListener('scroll', onScroll, true)
})

onBeforeUnmount(() => {
  if (settle) clearTimeout(settle)
  document.removeEventListener('mouseup', onUp)
  document.removeEventListener('selectionchange', onChange)
  window.removeEventListener('scroll', onScroll, true)
})
</script>

<template>
  <button
    v-if="at"
    type="button"
    class="sel-ask"
    data-agent-select="off"
    :style="{ top: `${at.top}px`, left: `${at.left}px` }"
    @mousedown.prevent
    @click="ask"
  >
    <v-icon size="16">mdi-creation-outline</v-icon>
    {{ t('tasks.assistant.context.askSelection') }}
  </button>
</template>

<style scoped>
.sel-ask {
  position: fixed;
  z-index: 1005;
  display: inline-flex;
  gap: 4px;
  align-items: center;
  min-height: 32px;
  padding: 0 10px;
  font-size: 13px;
  color: var(--ink);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  transform: translateX(-50%);
}

.sel-ask:hover {
  background: var(--fill-2);
}

.sel-ask:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
</style>
