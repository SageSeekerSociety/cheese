<script setup lang="ts">
// 颜色弹层：文字颜色与高亮各一份，色板相同、命令不同，靠 kind 分。按钮下沿一条
// 现在这一类上的颜色，没上色时是一条空槽。
import { computed, inject } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { CONTENT_COLORS } from './richTextOptions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

const props = defineProps<{ kind: 'color' | 'highlight' }>()
const { state, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))

/** 这一类现在是什么色，没有就是 null。 */
const current = computed(() => (props.kind === 'color' ? state.value.color : state.value.highlight))
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb rt-tb--swatch"
        :aria-label="label(kind)"
        :title="label(kind)"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">{{ kind === 'color' ? 'mdi-format-color-text' : 'mdi-format-color-highlight' }}</v-icon>
        <span class="rt-tb__bar" :class="{ 'is-empty': !current }" :style="{ background: current ?? undefined }" />
      </button>
    </template>
    <div class="rt-pop" @mousedown.prevent>
      <div class="rt-swatches">
        <button
          v-for="color in CONTENT_COLORS"
          :key="color"
          type="button"
          class="rt-swatch"
          :style="{ background: color }"
          :aria-label="color"
          :aria-pressed="current?.toLowerCase() === color"
          @click="pick((c) => (kind === 'color' ? c.setColor(color) : c.setHighlight({ color })))"
        />
      </div>
      <button
        type="button"
        class="rt-pop__item"
        @click="pick((c) => (kind === 'color' ? c.unsetColor() : c.unsetHighlight()))"
      >
        {{ label(kind === 'color' ? 'colorNone' : 'highlightNone') }}
      </button>
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
