<script setup lang="ts">
import type { SketchTool } from './designSketch'

import { ref } from 'vue'

import { DRAW_TOOLS, SKETCH_COLORS } from './designSketch'

import { t } from '@/i18n'

const props = defineProps<{
  tool: SketchTool
  color: string
  canUndo: boolean
  canRedo: boolean
  hasStrokes: boolean
  canSend: boolean
  /** 图片版本没验证过时框选没有意义，那颗按钮要跟着禁用。 */
  canSelect?: boolean
  busy?: boolean
}>()
/** 图上的字说不清「改成什么」，所以那一句和「加入对话」摆在一起。 */
const note = defineModel<string>('note', { default: '' })
/** 区域选择是外面那颗按钮，它开着时按 Esc 取消之后焦点要回得来。 */
const selectButton = ref<HTMLButtonElement | null>(null)
defineExpose({ selectButton })
const emit = defineEmits<{
  pick: [tool: SketchTool]
  recolor: [color: string]
  undo: []
  redo: []
  clear: []
  send: []
}>()
/** 回车发送。输入法组字中的那一次回车是「选词」，不是「发送」：放它过去，
 *  否则用中文打字的人每选一个词就把标注发出去一次。 */
function onNoteEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  emit('send')
}
/** 每个工具的示意图标；不引图标库，路径就写在这里，省一个依赖。 */
const GLYPH: Record<SketchTool, string> = {
  select: 'M3 3h6M3 3v6M15 3h-6M15 3v6M3 15v-6M15 15h-6M15 15v-6',
  pen: 'M3 17c3-1 5-3 6-6s3-6 6-8',
  line: 'M3 15 15 5',
  arrow: 'M3 15 15 5M15 5h-5M15 5v5',
  rect: 'M3 5h12v10H3z',
  ellipse: 'M9 5c3.3 0 6 2.2 6 5s-2.7 5-6 5-6-2.2-6-5 2.7-5 6-5z',
  text: 'M4 5h10M9 5v10M6.5 15h5',
  redact: 'M3 7h12v6H3z',
}
</script>

<template>
  <div class="sketch-toolbar" role="group" :aria-label="t('design.sketchTools')">
    <button
      ref="selectButton"
      type="button"
      class="sketch-toolbar__tool"
      :class="{ 'is-active': props.tool === 'select' }"
      :disabled="props.canSelect === false"
      :aria-pressed="props.tool === 'select'"
      :aria-label="t('design.tools.select')"
      :title="t('design.tools.select')"
      @click="emit('pick', 'select')"
    >
      <svg viewBox="0 0 18 20" width="16" height="18" aria-hidden="true">
        <path :d="GLYPH.select" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>
    <button
      v-for="item in DRAW_TOOLS"
      :key="item"
      type="button"
      class="sketch-toolbar__tool"
      :class="{ 'is-active': props.tool === item }"
      :aria-pressed="props.tool === item"
      :aria-label="t(`design.tools.${item}`)"
      :title="t(`design.tools.${item}`)"
      @click="emit('pick', item)"
    >
      <svg viewBox="0 0 18 20" width="16" height="18" aria-hidden="true">
        <path :d="GLYPH[item]" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      </svg>
    </button>
    <span class="sketch-toolbar__gap" />
    <button
      v-for="swatch in SKETCH_COLORS"
      :key="swatch"
      type="button"
      class="sketch-toolbar__color"
      :class="{ 'is-active': props.color === swatch }"
      :style="{ color: swatch }"
      :aria-pressed="props.color === swatch"
      :aria-label="t('design.sketchColor', { color: swatch })"
      @click="emit('recolor', swatch)"
    >
      ●
    </button>
    <span class="sketch-toolbar__gap" />
    <button
      type="button"
      :disabled="!canUndo"
      aria-keyshortcuts="Meta+Z Control+Z"
      :title="t('design.undoShortcut')"
      @click="emit('undo')"
    >
      {{ t('design.undo') }}
    </button>
    <button
      type="button"
      :disabled="!canRedo"
      aria-keyshortcuts="Meta+Shift+Z Control+Shift+Z Control+Y"
      :title="t('design.redoShortcut')"
      @click="emit('redo')"
    >
      {{ t('design.redo') }}
    </button>
    <button type="button" :disabled="!hasStrokes" @click="emit('clear')">{{ t('design.clear') }}</button>
    <input
      v-if="hasStrokes"
      v-model="note"
      type="text"
      class="sketch-toolbar__note"
      autocomplete="off"
      :placeholder="t('design.notePlaceholder')"
      :aria-label="t('design.notePlaceholder')"
      @keydown.enter="onNoteEnter"
    />
    <button type="button" class="is-primary" :disabled="!canSend || busy" @click="emit('send')">
      {{ t('design.addToChat') }}
    </button>
  </div>
</template>

<style scoped>
.sketch-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}
.sketch-toolbar__gap {
  width: 8px;
}
.sketch-toolbar button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
}
.sketch-toolbar button:hover:not(:disabled) {
  background: var(--fill-2);
}
.sketch-toolbar button:disabled {
  color: var(--faint);
}
.sketch-toolbar button.is-active {
  background: var(--fill-2);
  color: var(--accent);
}
.sketch-toolbar button.is-primary {
  margin-left: auto;
  background: var(--accent);
  color: #fff;
}
.sketch-toolbar button.is-primary:disabled {
  background: var(--fill-2);
  color: var(--faint);
}
.sketch-toolbar__tool {
  display: inline-flex;
  align-items: center;
}
.sketch-toolbar__color {
  font-size: 15px;
  line-height: 1;
}
.sketch-toolbar__color.is-active {
  outline: 2px solid var(--accent);
}
.sketch-toolbar__note {
  flex: 1 1 160px;
  min-width: 120px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
}
.sketch-toolbar__note:focus-visible {
  outline: 2px solid var(--accent);
}
</style>
