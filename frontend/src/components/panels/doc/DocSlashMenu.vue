<script setup lang="ts">
// Slash 菜单的浮层。锚点用 suggestion 给的 caret rect，和文档里其他浮层一样是
// 相对 .doc-editor-wrap 定位的，所以它跟着内容滚。
//
// 键盘（↑↓ / Enter / Esc）归 suggestion 插件管，这里只画和转发鼠标：两条路最后
// 都走同一个 command()，「/query」那段文本才会被一致地删掉。
import type { SlashItem } from '../../../lib/docSlashMenu'

import { nextTick, ref, watch } from 'vue'

const props = defineProps<{
  items: SlashItem[]
  /** Which row the keyboard is on — the mouse moves it too, Notion-style. */
  index: number
  top: number
  left: number
}>()

const emit = defineEmits<{
  (e: 'pick', item: SlashItem): void
  (e: 'hover', index: number): void
}>()

// 键盘走到列表外面去的时候得跟着滚。这件事归这里，因为要滚的是这个组件自己的
// DOM —— 让宿主拿着 ref 去 querySelector 一个 BEM 类名，是把内部结构泄出去。
const menuEl = ref<HTMLElement | null>(null)
watch(
  () => props.index,
  () => {
    void nextTick(() => {
      menuEl.value?.querySelector('.doc-slash__item--active')?.scrollIntoView({ block: 'nearest' })
    })
  }
)
</script>

<template>
  <div ref="menuEl" class="doc-slash__menu" :style="{ top: `${top}px`, left: `${left}px` }">
    <button
      v-for="(it, i) in items"
      :key="it.key"
      type="button"
      class="doc-slash__item"
      :class="{ 'doc-slash__item--active': i === index }"
      @mousedown.prevent
      @mouseenter="emit('hover', i)"
      @click="emit('pick', it)"
    >
      <v-icon size="15" class="doc-slash__icon">{{ it.icon }}</v-icon>
      <span class="doc-slash__label">{{ it.label }}</span>
      <span class="doc-slash__hint">{{ it.hint }}</span>
    </button>
  </div>
</template>

<style scoped>
.doc-slash__menu {
  position: absolute;
  z-index: 7;
  display: flex;
  flex-direction: column;
  min-width: 196px;
  max-height: 300px;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: var(--shadow-2);
  padding: 4px;
}
.doc-slash__item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: none;
  text-align: left;
  font-size: 13px;
  color: var(--ink);
  padding: 6px 9px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
}
.doc-slash__item--active {
  background: var(--fill);
}
.doc-slash__icon {
  color: var(--muted);
  flex: 0 0 auto;
}
.doc-slash__label {
  flex: 1 1 auto;
}
.doc-slash__hint {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--faint);
}
</style>
