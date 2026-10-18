<script setup lang="ts">
// 表格弹层：没在表格里时选行列数插一张，在表格里时是行列操作。
import { inject, reactive } from 'vue'

import { MENU_Z, tableActions, toolbarLabel as label } from './richTextActions'
import { RICH_TEXT_TOOLBAR, useRichTextMenu } from './useRichTextState'

import { t } from '@/i18n'

const { state, can, open, pick } = useRichTextMenu(inject(RICH_TEXT_TOOLBAR))

// 选行列数的格子：八乘八，划过时先点亮，移开就空掉。
const GRID = 8
const grid = reactive({ rows: 0, cols: 0, header: true })

function insertTable(rows: number, cols: number) {
  pick((c) => c.insertTable({ rows, cols, withHeaderRow: grid.header }))
}
</script>

<template>
  <v-menu v-model="open" :z-index="MENU_Z" location="bottom start" :close-on-content-click="false">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="rt-tb rt-tb--menu"
        :aria-label="label('table')"
        :title="label('table')"
        :aria-pressed="state.inTable"
        :disabled="!state.editable"
        @mousedown.prevent
      >
        <v-icon size="17">mdi-table</v-icon><v-icon size="14">mdi-menu-down</v-icon>
      </button>
    </template>
    <div v-if="state.inTable" class="rt-pop rt-pop--list" @mousedown.prevent>
      <button
        v-for="a in tableActions"
        :key="a.key"
        type="button"
        class="rt-pop__item"
        :disabled="!can(a.command)"
        @click="pick(a.command)"
      >
        {{ label(a.key) }}
      </button>
    </div>
    <div v-else class="rt-pop" @mousedown.prevent>
      <!-- Only a container: each cell is its own button. Leaving the grid by pointer or by Tab clears the highlight. -->
      <div
        class="rt-grid"
        role="presentation"
        @mouseleave="grid.rows = grid.cols = 0"
        @focusout="grid.rows = grid.cols = 0"
      >
        <template v-for="r in GRID" :key="r">
          <button
            v-for="c in GRID"
            :key="`${r}-${c}`"
            type="button"
            class="rt-grid__cell"
            :class="{ 'is-on': r <= grid.rows && c <= grid.cols }"
            :aria-label="t('editor.toolbar.tableSize', { rows: r, cols: c })"
            @mouseenter="Object.assign(grid, { rows: r, cols: c })"
            @focus="Object.assign(grid, { rows: r, cols: c })"
            @click="insertTable(r, c)"
          />
        </template>
      </div>
      <div class="rt-grid__size t-meta-read">
        {{ grid.rows ? t('editor.toolbar.tableSize', { rows: grid.rows, cols: grid.cols }) : label('table') }}
      </div>
      <v-checkbox v-model="grid.header" :label="label('tableHeader')" density="compact" hide-details />
    </div>
  </v-menu>
</template>

<style scoped src="./richTextToolbar.css"></style>
