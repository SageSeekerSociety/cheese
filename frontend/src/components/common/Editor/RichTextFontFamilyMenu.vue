<script setup lang="ts">
// 字体弹层。每一项按字体名本身显示。
import { inject } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { FONT_FAMILIES } from './richTextOptions'
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
        :aria-label="label('fontFamily')"
        :title="label('fontFamily')"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-format-font</v-icon><v-icon size="14">mdi-menu-down</v-icon>
      </button>
    </template>
    <div class="rt-pop rt-pop--list" @mousedown.prevent>
      <button
        type="button"
        class="rt-pop__item"
        :aria-pressed="!state.fontFamily"
        @click="pick((c) => c.unsetFontFamily())"
      >
        {{ label('default') }}
      </button>
      <button
        v-for="font in FONT_FAMILIES"
        :key="font"
        type="button"
        class="rt-pop__item"
        :style="{ fontFamily: font }"
        :aria-pressed="state.fontFamily === font"
        @click="pick((c) => c.setFontFamily(font))"
      >
        {{ font }}
      </button>
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
