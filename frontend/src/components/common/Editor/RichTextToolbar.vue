<script setup lang="ts">
// 存 JSON / HTML 的那几处富文本（题目详情、知识库、空间公告与模板、团队简介）的工具栏。
// 能力与原先那台编辑器的工具栏一一对应；颜色、字号、字体、标题、对齐、表格、链接收进
// 小弹层，按钮本身一排排开，窄了就横向滚动。
//
// 实况文档不用这一排：它存 Markdown，颜色、字号、对齐这些存不下（那边是 DocFormatToolbar）。
//
// 这一份只管摆顺序：动作表在 richTextActions.ts，编辑器状态在 useRichTextState.ts（由它
// provide 给下面这些子件），样式在 richTextToolbar.css，每个弹层一个子件。
import type { Editor } from '@tiptap/core'

import { provide, ref, toRaw } from 'vue'

import RichTextActionGroup from './RichTextActionGroup.vue'
import { createToolbarActions, historyActions, toolbarLabel as label } from './richTextActions'
import RichTextAlignMenu from './RichTextAlignMenu.vue'
import RichTextColorMenu from './RichTextColorMenu.vue'
import RichTextFontFamilyMenu from './RichTextFontFamilyMenu.vue'
import RichTextFontSizeMenu from './RichTextFontSizeMenu.vue'
import RichTextHeadingMenu from './RichTextHeadingMenu.vue'
import RichTextLinkMenu from './RichTextLinkMenu.vue'
import RichTextTableMenu from './RichTextTableMenu.vue'
import RichTextVideoMenu from './RichTextVideoMenu.vue'
import { RICH_TEXT_TOOLBAR, useRichTextState } from './useRichTextState'

const props = defineProps<{
  editor: Editor | null | undefined
  /** 没有附件来源（预览、测试）时没有插图这一项。 */
  canInsertImage: boolean
  uploading: boolean
  fullscreen: boolean
}>()
const emit = defineEmits<{ (e: 'insert-image'): void; (e: 'toggle-fullscreen'): void }>()

const { state, active, can, run } = useRichTextState(() => props.editor)
provide(RICH_TEXT_TOOLBAR, { state, active, can, run })

const { markActions, blockActions, insertActions } = createToolbarActions(active)

// ---- 左右方向键在按钮之间走，Esc 回到正文。
const toolbar = ref<HTMLElement | null>(null)
function moveFocus(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    toRaw(props.editor)?.commands.focus(undefined, { scrollIntoView: false })
    return
  }
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  const buttons = Array.from(toolbar.value?.querySelectorAll<HTMLButtonElement>('button.rt-tb') ?? [])
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  if (index === -1) return
  event.preventDefault()
  const next = buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length]
  next.focus({ preventScroll: true })
  next.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the ARIA toolbar pattern: arrow keys bubble here from the focused button and move focus to the next one -->
  <div
    ref="toolbar"
    class="rt-toolbar"
    role="toolbar"
    :aria-label="label('label')"
    @mousedown.self.prevent
    @keydown="moveFocus"
  >
    <RichTextActionGroup :actions="historyActions" />
    <span class="rt-tb__sep" />

    <RichTextHeadingMenu />
    <RichTextFontFamilyMenu />
    <RichTextFontSizeMenu />
    <span class="rt-tb__sep" />

    <RichTextActionGroup :actions="markActions" />
    <RichTextColorMenu kind="color" />
    <RichTextColorMenu kind="highlight" />
    <span class="rt-tb__sep" />

    <RichTextAlignMenu />
    <RichTextActionGroup :actions="blockActions" />
    <span class="rt-tb__sep" />

    <RichTextLinkMenu />
    <button
      v-if="canInsertImage"
      type="button"
      class="rt-tb"
      :aria-label="label('image')"
      :title="label('image')"
      :disabled="!state.editable || uploading"
      @mousedown.prevent
      @click="emit('insert-image')"
    >
      <v-icon size="17">mdi-image-plus-outline</v-icon>
    </button>
    <RichTextVideoMenu />
    <RichTextTableMenu />
    <RichTextActionGroup :actions="insertActions" />
    <span class="rt-tb__sep" />

    <button
      type="button"
      class="rt-tb"
      :aria-label="label(fullscreen ? 'exitFullscreen' : 'fullscreen')"
      :title="label(fullscreen ? 'exitFullscreen' : 'fullscreen')"
      :aria-pressed="fullscreen"
      @mousedown.prevent
      @click="emit('toggle-fullscreen')"
    >
      <v-icon size="17">{{ fullscreen ? 'mdi-fullscreen-exit' : 'mdi-fullscreen' }}</v-icon>
    </button>
  </div>
</template>

<style scoped src="./richTextToolbar.css"></style>
