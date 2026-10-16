<script setup lang="ts">
// 标题弹层：正文，与一至六级标题。
import { inject } from 'vue'

import { MENU_Z, toolbarLabel as label } from './richTextActions'
import { HEADING_LEVELS } from './richTextOptions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

import { t } from '@/i18n'

const { state, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb rt-tb--menu"
        :aria-label="label('heading')"
        :title="label('heading')"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-format-header-pound</v-icon><v-icon size="14">mdi-menu-down</v-icon>
      </button>
    </template>
    <div class="rt-pop rt-pop--list" @mousedown.prevent>
      <button
        type="button"
        class="rt-pop__item"
        :aria-pressed="state.heading === null"
        @click="pick((c) => c.setParagraph())"
      >
        {{ label('paragraph') }}
      </button>
      <button
        v-for="level in HEADING_LEVELS"
        :key="level"
        type="button"
        class="rt-pop__item"
        :class="`rt-pop__item--h${level}`"
        :aria-pressed="state.heading === level"
        @click="pick((c) => c.setHeading({ level }))"
      >
        {{ t('editor.toolbar.headingLevel', { level }) }}
      </button>
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
