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
      menuEl.value?.querySelector('.doc-menu__item.is-active')?.scrollIntoView({ block: 'nearest' })
    })
  }
)
</script>

<template>
  <div ref="menuEl" class="doc-menu doc-slash__menu" role="menu" :style="{ top: `${top}px`, left: `${left}px` }">
    <button
      v-for="(it, i) in items"
      :key="it.key"
      type="button"
      role="menuitem"
      class="doc-menu__item"
      :class="{ 'is-active': i === index }"
      @mousedown.prevent
      @mouseenter="emit('hover', i)"
      @click="emit('pick', it)"
    >
      <v-icon size="16">{{ it.icon }}</v-icon>
      <span class="doc-menu__label">{{ it.label }}</span>
      <span class="doc-menu__hint">{{ it.hint }}</span>
    </button>
  </div>
</template>

<style scoped>
/* 外观在 styles/docBlocks.css 的 .doc-menu：这里只管摆在哪、多高。 */
.doc-slash__menu {
  position: absolute;
  z-index: var(--z-raised-7);
  min-width: 196px;
  max-height: 300px;
  overflow-y: auto;
}
.doc-menu__hint {
  padding-left: 16px;
}
</style>
