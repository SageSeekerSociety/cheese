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
/**
 * 工具栏只画图标，名字一律走 `aria-label` 和 `title`。
 *
 * 一排十六颗按钮里，中文标签比图标宽一倍多，窄面板（240px）里横滚的距离随之翻倍；
 * 而这几件事各网站都用同一套图形。名字没被丢掉，只是移到了悬停提示和读屏里——
 * 「加入对话」除外，它是提交动作不是工具，留着文字更清楚（参考物也写着 Add to chat）。
 */
const TOOL_ICON: Record<SketchTool, string> = {
  select: 'mdi-cursor-default-outline',
  pen: 'mdi-pencil',
  line: 'mdi-vector-line',
  arrow: 'mdi-arrow-top-right',
  rect: 'mdi-rectangle-outline',
  ellipse: 'mdi-ellipse-outline',
  text: 'mdi-format-text',
  redact: 'mdi-eye-off-outline',
}
</script>

<template>
  <div class="sketch-toolbar" role="group" :aria-label="t('design.sketchTools')">
    <!-- 工具、颜色、历史挤在这一段里横滚；「说一句要改什么」和「加入对话」钉在
         右边不跟着滚——它们是这一步的落点，滚出去就等于按不到。 -->
    <div class="sketch-toolbar__scroll">
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
        <i :class="`mdi ${TOOL_ICON.select}`" class="sketch-toolbar__icon" aria-hidden="true" />
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
        <i :class="`mdi ${TOOL_ICON[item]}`" class="sketch-toolbar__icon" aria-hidden="true" />
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
        class="sketch-toolbar__tool"
        :disabled="!canUndo"
        aria-keyshortcuts="Meta+Z Control+Z"
        :aria-label="t('design.undo')"
        :title="t('design.undoShortcut')"
        @click="emit('undo')"
      >
        <i class="mdi mdi-undo sketch-toolbar__icon" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="sketch-toolbar__tool"
        :disabled="!canRedo"
        aria-keyshortcuts="Meta+Shift+Z Control+Shift+Z Control+Y"
        :aria-label="t('design.redo')"
        :title="t('design.redoShortcut')"
        @click="emit('redo')"
      >
        <i class="mdi mdi-redo sketch-toolbar__icon" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="sketch-toolbar__tool"
        :disabled="!hasStrokes"
        :aria-label="t('design.clear')"
        :title="t('design.clear')"
        @click="emit('clear')"
      >
        <i class="mdi mdi-delete-sweep-outline sketch-toolbar__icon" aria-hidden="true" />
      </button>
    </div>
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
  /* 窄面板（240px）里让它换行会占掉三四行，图就没地方了——排成一行，超出的横滚。 */
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
/* 只有这一段横滚。右边那两样（说明和提交）留在外面，窄面板里也一直看得见。 */
.sketch-toolbar__scroll {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
}
.sketch-toolbar__gap {
  width: 8px;
}
.sketch-toolbar button {
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.sketch-toolbar button:hover:not(:disabled) {
  background: var(--fill-2);
}
/* 禁用态光靠颜色深浅分不出来：#747a82 的灰和正文的灰在窄条上差别很小，而这几颗
 * 按钮「现在能不能按」正是撤销/重做要回答的问题。淡到看得见的一档，加上默认光标。 */
.sketch-toolbar button:disabled {
  color: var(--faint);
  opacity: 0.45;
  cursor: default;
}
.sketch-toolbar button.is-active {
  background: var(--fill-2);
  color: var(--accent);
}
.sketch-toolbar button.is-primary {
  flex: 0 0 auto;
  background: var(--accent);
  /* 和发送键同一对：琥珀底上的字 */
  color: rgb(var(--v-theme-on-primary));
}
.sketch-toolbar button.is-primary:disabled {
  background: var(--fill-2);
  color: var(--faint);
}
.sketch-toolbar__tool {
  display: inline-flex;
  align-items: center;
}
/* 图标是字体字形，尺寸跟着 font-size 走；不设为 1 的话行高会把这一条顶高几像素。 */
.sketch-toolbar__icon {
  font-size: 18px;
  line-height: 1;
}
.sketch-toolbar__color {
  font-size: 15px;
  line-height: 1;
}
.sketch-toolbar__color.is-active {
  outline: 2px solid var(--accent);
}
.sketch-toolbar__note {
  /* 宽的时候 160px，窄的时候让给提交键——它有下界，不会被压没。 */
  flex: 0 1 160px;
  min-width: 88px;
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
