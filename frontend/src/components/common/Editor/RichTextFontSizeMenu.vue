<script setup lang="ts">
// 字号弹层。存的是不带单位的字符串，与老内容一样。
import { inject } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { FONT_SIZES } from './richTextOptions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

const { state, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb rt-tb--menu"
        :aria-label="label('fontSize')"
        :title="label('fontSize')"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-format-size</v-icon><v-icon size="14">mdi-menu-down</v-icon>
      </button>
    </template>
    <div class="rt-pop rt-pop--list" @mousedown.prevent>
      <button
        type="button"
        class="rt-pop__item"
        :aria-pressed="!state.fontSize"
        @click="pick((c) => c.unsetFontSize())"
      >
        {{ label('default') }}
      </button>
      <button
        v-for="size in FONT_SIZES"
        :key="size"
        type="button"
        class="rt-pop__item t-num"
        :aria-pressed="state.fontSize === String(size)"
        @click="pick((c) => c.setFontSize(String(size)))"
      >
        {{ size }}
      </button>
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
