<script setup lang="ts">
// 工具栏上一段口令按钮：历史、字符格式、块、插入各一段，样子一样，只有动作表不同。
// 选中态只有带 isActive 的那些有，历史那一段因此不画 aria-pressed（与原来一致）。

import type { ToolbarAction } from './richTextActions'

import { inject } from 'vue'

import { toolbarLabel as label } from './richTextActions'
import { RICH_TEXT_TOOLBAR, useRichTextControls } from './useRichTextState'

defineProps<{ actions: ToolbarAction[] }>()
const { can, run } = useRichTextControls(inject(RICH_TEXT_TOOLBAR))
</script>

<template>
  <button
    v-for="a in actions"
    :key="a.key"
    type="button"
    class="rt-tb"
    :aria-label="label(a.key)"
    :title="label(a.key)"
    :aria-pressed="a.isActive ? a.isActive() : undefined"
    :disabled="!can(a.command)"
    @mousedown.prevent
    @click="run(a.command)"
  >
    <v-icon size="17">{{ a.icon }}</v-icon>
  </button>
</template>

<style scoped src="./richTextToolbar.css"></style>
