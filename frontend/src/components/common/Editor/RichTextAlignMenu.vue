<script setup lang="ts">
// 对齐弹层：按钮上是现在这一种，里面是四种。
import { inject } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { ALIGNMENTS } from './richTextOptions'
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
        :aria-label="label('align')"
        :title="label('align')"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">{{ ALIGNMENTS.find((a) => a.value === state.align)?.icon ?? 'mdi-format-align-left' }}</v-icon
        ><v-icon size="14">mdi-menu-down</v-icon>
      </button>
    </template>
    <div class="rt-pop rt-pop--list" @mousedown.prevent>
      <button
        v-for="a in ALIGNMENTS"
        :key="a.value"
        type="button"
        class="rt-pop__item"
        :aria-pressed="state.align === a.value"
        @click="pick((c) => c.setTextAlign(a.value))"
      >
        <v-icon size="17">{{ a.icon }}</v-icon>
        {{ label(a.key) }}
      </button>
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
