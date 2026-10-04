<script setup lang="ts">
// 存 JSON / HTML 的那几处富文本（题目详情、知识库、空间公告与模板、团队简介）的工具栏。
// 能力与原先那台编辑器的工具栏一一对应；颜色、字号、字体、标题、对齐、表格、链接收进
// 小弹层，按钮本身一排排开，窄了就横向滚动。
//
// 实况文档不用这一排：它存 Markdown，颜色、字号、对齐这些存不下（那边是 DocFormatToolbar）。
import type { ChainedCommands, Editor } from '@tiptap/core'

import { computed, reactive, ref, toRaw, watch } from 'vue'

import { ALIGNMENTS, CONTENT_COLORS, FONT_FAMILIES, FONT_SIZES, HEADING_LEVELS } from './richTextOptions'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  editor: Editor | null | undefined
  /** 没有附件来源（预览、测试）时没有插图这一项。 */
  canInsertImage: boolean
  uploading: boolean
  fullscreen: boolean
}>()
const emit = defineEmits<{ (e: 'insert-image'): void; (e: 'toggle-fullscreen'): void }>()

// 编辑器的状态不是响应式的：每一笔事务后数一下，按钮的选中态、可用态跟着重算。
const revision = ref(0)
watch(
  () => toRaw(props.editor),
  (editor, _previous, cleanup) => {
    if (!editor) return
    const update = () => revision.value++
    editor.on('transaction', update)
    cleanup(() => editor.off('transaction', update))
  },
  { immediate: true }
)

/** 读编辑器状态的地方都从这里拿，好让它跟着事务重算。 */
const state = computed(() => {
  void revision.value
  const editor = toRaw(props.editor) ?? null
  const textStyle = editor?.getAttributes('textStyle') ?? {}
  return {
    editor,
    editable: !!editor?.isEditable,
    heading: HEADING_LEVELS.find((level) => editor?.isActive('heading', { level })) ?? null,
    align: ALIGNMENTS.find((a) => editor?.isActive({ textAlign: a.value }))?.value ?? null,
    fontFamily: (textStyle.fontFamily as string | null | undefined) ?? null,
    fontSize: (textStyle.fontSize as string | null | undefined) ?? null,
    color: (textStyle.color as string | null | undefined) ?? null,
    highlight: (editor?.getAttributes('highlight').color as string | null | undefined) ?? null,
    inTable: !!editor?.isActive('table'),
    link: !!editor?.isActive('link'),
  }
})

function active(name: string, attrs?: Record<string, unknown>) {
  void revision.value
  return !!toRaw(props.editor)?.isActive(name, attrs)
}

/** 现在这条命令做不做得成：做不成的按钮置灰。 */
function can(command: (c: ChainedCommands) => ChainedCommands) {
  void revision.value
  const editor = toRaw(props.editor)
  return !!editor?.isEditable && command(editor.can().chain()).run()
}

function run(command: (c: ChainedCommands) => ChainedCommands) {
  const editor = toRaw(props.editor)
  if (!editor?.isEditable) return
  command(editor.chain().focus()).run()
}

interface Simple {
  key: string
  icon: string
  isActive?: () => boolean
  command: (c: ChainedCommands) => ChainedCommands
}

const historyActions: Simple[] = [
  { key: 'undo', icon: 'mdi-undo', command: (c) => c.undo() },
  { key: 'redo', icon: 'mdi-redo', command: (c) => c.redo() },
  { key: 'clear', icon: 'mdi-format-clear', command: (c) => c.unsetAllMarks().clearNodes() },
]
const markActions: Simple[] = [
  { key: 'bold', icon: 'mdi-format-bold', isActive: () => active('bold'), command: (c) => c.toggleBold() },
  { key: 'italic', icon: 'mdi-format-italic', isActive: () => active('italic'), command: (c) => c.toggleItalic() },
  {
    key: 'underline',
    icon: 'mdi-format-underline',
    isActive: () => active('underline'),
    command: (c) => c.toggleUnderline(),
  },
  {
    key: 'strike',
    icon: 'mdi-format-strikethrough',
    isActive: () => active('strike'),
    command: (c) => c.toggleStrike(),
  },
  { key: 'code', icon: 'mdi-code-tags', isActive: () => active('code'), command: (c) => c.toggleCode() },
  {
    key: 'superscript',
    icon: 'mdi-format-superscript',
    isActive: () => active('superscript'),
    command: (c) => c.toggleSuperscript(),
  },
  {
    key: 'subscript',
    icon: 'mdi-format-subscript',
    isActive: () => active('subscript'),
    command: (c) => c.toggleSubscript(),
  },
]
const listItemType = () => (active('taskItem') ? 'taskItem' : 'listItem')
const blockActions: Simple[] = [
  {
    key: 'bullet',
    icon: 'mdi-format-list-bulleted',
    isActive: () => active('bulletList'),
    command: (c) => c.toggleBulletList(),
  },
  {
    key: 'ordered',
    icon: 'mdi-format-list-numbered',
    isActive: () => active('orderedList'),
    command: (c) => c.toggleOrderedList(),
  },
  {
    key: 'task',
    icon: 'mdi-format-list-checks',
    isActive: () => active('taskList'),
    command: (c) => c.toggleTaskList(),
  },
  { key: 'indent', icon: 'mdi-format-indent-increase', command: (c) => c.sinkListItem(listItemType()) },
  { key: 'outdent', icon: 'mdi-format-indent-decrease', command: (c) => c.liftListItem(listItemType()) },
]
const insertActions: Simple[] = [
  {
    key: 'quote',
    icon: 'mdi-format-quote-close',
    isActive: () => active('blockquote'),
    command: (c) => c.toggleBlockquote(),
  },
  { key: 'horizontalRule', icon: 'mdi-minus', command: (c) => c.setHorizontalRule() },
  {
    key: 'codeBlock',
    icon: 'mdi-code-braces',
    isActive: () => active('codeBlock'),
    command: (c) => c.toggleCodeBlock(),
  },
]

// 弹层要盖得住全屏的编辑器（TipTapEditor 里那一层是 2450，在弹窗 2400 之上）。
// z-index 属性只收数字，读不到 CSS token，这里是 `--z-menu`（design-system §3.8）在 JS
// 里的唯一一份。
const MENU_Z = 2500

const label = (key: string) => t(`editor.toolbar.${key}`)

// ---- 弹层。同一时刻只开一个。
const open = reactive<Record<string, boolean>>({})

function pick(menu: string, command: (c: ChainedCommands) => ChainedCommands) {
  open[menu] = false
  run(command)
}

// ---- 链接：打开时带出光标所在链接的地址。
const link = reactive({ href: '', newTab: true })
watch(
  () => open.link,
  (isOpen) => {
    if (!isOpen) return
    const attrs = toRaw(props.editor)?.getAttributes('link') ?? {}
    link.href = (attrs.href as string | undefined) ?? ''
    link.newTab = attrs.target ? attrs.target === '_blank' : true
  }
)
function applyLink() {
  const href = link.href.trim()
  open.link = false
  if (!href) return
  const target = link.newTab ? '_blank' : '_self'
  const editor = toRaw(props.editor)
  if (!editor) return
  if (editor.state.selection.empty && !editor.isActive('link')) {
    run((c) => c.insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href, target } }] }))
  } else {
    run((c) => c.extendMarkRange('link').setLink({ href, target }))
  }
}
function removeLink() {
  open.link = false
  run((c) => c.extendMarkRange('link').unsetLink())
}

// ---- 表格：没在表格里时选行列数插一张；在表格里时是行列操作。
const GRID = 8
const grid = reactive({ rows: 0, cols: 0, header: true })
function insertTable(rows: number, cols: number) {
  pick('table', (c) => c.insertTable({ rows, cols, withHeaderRow: grid.header }))
}
const tableActions: { key: string; command: (c: ChainedCommands) => ChainedCommands }[] = [
  { key: 'addRowBefore', command: (c) => c.addRowBefore() },
  { key: 'addRowAfter', command: (c) => c.addRowAfter() },
  { key: 'addColumnBefore', command: (c) => c.addColumnBefore() },
  { key: 'addColumnAfter', command: (c) => c.addColumnAfter() },
  { key: 'deleteRow', command: (c) => c.deleteRow() },
  { key: 'deleteColumn', command: (c) => c.deleteColumn() },
  { key: 'mergeOrSplit', command: (c) => c.mergeOrSplit() },
  { key: 'deleteTable', command: (c) => c.deleteTable() },
]

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
  <div
    ref="toolbar"
    class="rt-toolbar"
    role="toolbar"
    :aria-label="label('label')"
    @mousedown.self.prevent
    @keydown="moveFocus"
  >
    <button
      v-for="a in historyActions"
      :key="a.key"
      type="button"
      class="rt-tb"
      :aria-label="label(a.key)"
      :title="label(a.key)"
      :disabled="!can(a.command)"
      @mousedown.prevent
      @click="run(a.command)"
    >
      <v-icon size="17">{{ a.icon }}</v-icon>
    </button>
    <span class="rt-tb__sep" />

    <v-menu v-model="open.heading" :z-index="MENU_Z" location="bottom start">
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
          @click="pick('heading', (c) => c.setParagraph())"
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
          @click="pick('heading', (c) => c.setHeading({ level }))"
        >
          {{ t('editor.toolbar.headingLevel', { level }) }}
        </button>
      </div>
    </v-menu>

    <v-menu v-model="open.fontFamily" :z-index="MENU_Z" location="bottom start">
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
          @click="pick('fontFamily', (c) => c.unsetFontFamily())"
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
          @click="pick('fontFamily', (c) => c.setFontFamily(font))"
        >
          {{ font }}
        </button>
      </div>
    </v-menu>

    <v-menu v-model="open.fontSize" :z-index="MENU_Z" location="bottom start">
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
          @click="pick('fontSize', (c) => c.unsetFontSize())"
        >
          {{ label('default') }}
        </button>
        <button
          v-for="size in FONT_SIZES"
          :key="size"
          type="button"
          class="rt-pop__item t-num"
          :aria-pressed="state.fontSize === String(size)"
          @click="pick('fontSize', (c) => c.setFontSize(String(size)))"
        >
          {{ size }}
        </button>
      </div>
    </v-menu>
    <span class="rt-tb__sep" />

    <button
      v-for="a in markActions"
      :key="a.key"
      type="button"
      class="rt-tb"
      :aria-label="label(a.key)"
      :title="label(a.key)"
      :aria-pressed="a.isActive?.() ?? false"
      :disabled="!can(a.command)"
      @mousedown.prevent
      @click="run(a.command)"
    >
      <v-icon size="17">{{ a.icon }}</v-icon>
    </button>

    <v-menu
      v-for="kind in ['color', 'highlight'] as const"
      :key="kind"
      v-model="open[kind]"
      :z-index="MENU_Z"
      location="bottom start"
    >
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
          <span
            class="rt-tb__bar"
            :class="{ 'is-empty': !(kind === 'color' ? state.color : state.highlight) }"
            :style="{ background: (kind === 'color' ? state.color : state.highlight) ?? undefined }"
          />
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
            :aria-pressed="(kind === 'color' ? state.color : state.highlight)?.toLowerCase() === color"
            @click="pick(kind, (c) => (kind === 'color' ? c.setColor(color) : c.setHighlight({ color })))"
          />
        </div>
        <button
          type="button"
          class="rt-pop__item"
          @click="pick(kind, (c) => (kind === 'color' ? c.unsetColor() : c.unsetHighlight()))"
        >
          {{ label(kind === 'color' ? 'colorNone' : 'highlightNone') }}
        </button>
      </div>
    </v-menu>
    <span class="rt-tb__sep" />

    <v-menu v-model="open.align" :z-index="MENU_Z" location="bottom start">
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
          <v-icon size="17">{{
            ALIGNMENTS.find((a) => a.value === state.align)?.icon ?? 'mdi-format-align-left'
          }}</v-icon
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
          @click="pick('align', (c) => c.setTextAlign(a.value))"
        >
          <v-icon size="17">{{ a.icon }}</v-icon>
          {{ label(a.key) }}
        </button>
      </div>
    </v-menu>
    <button
      v-for="a in blockActions"
      :key="a.key"
      type="button"
      class="rt-tb"
      :aria-label="label(a.key)"
      :title="label(a.key)"
      :aria-pressed="a.isActive?.() ?? false"
      :disabled="!can(a.command)"
      @mousedown.prevent
      @click="run(a.command)"
    >
      <v-icon size="17">{{ a.icon }}</v-icon>
    </button>
    <span class="rt-tb__sep" />

    <v-menu v-model="open.link" :z-index="MENU_Z" location="bottom start" :close-on-content-click="false">
      <template #activator="{ props: menu }">
        <button
          v-bind="menu"
          type="button"
          class="rt-tb"
          :aria-label="label('link')"
          :title="label('link')"
          :aria-pressed="state.link"
          :disabled="!state.editable"
          @mousedown.prevent
        >
          <v-icon size="17">mdi-link-variant</v-icon>
        </button>
      </template>
      <form class="rt-pop rt-pop--form" @submit.prevent="applyLink">
        <v-text-field
          v-model="link.href"
          :label="label('linkHref')"
          placeholder="https://"
          density="compact"
          variant="outlined"
          hide-details
          autofocus
          autocomplete="off"
        />
        <v-checkbox v-model="link.newTab" :label="label('linkNewTab')" density="compact" hide-details />
        <div class="rt-pop__actions">
          <BaseButton v-if="state.link" kind="ghost" size="sm" @click="removeLink">{{ label('unlink') }}</BaseButton>
          <BaseButton type="submit" kind="primary" size="sm">{{ label('linkApply') }}</BaseButton>
        </div>
      </form>
    </v-menu>
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
    <v-menu v-model="open.table" :z-index="MENU_Z" location="bottom start" :close-on-content-click="false">
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
          @click="pick('table', a.command)"
        >
          {{ label(a.key) }}
        </button>
      </div>
      <div v-else class="rt-pop" @mousedown.prevent>
        <div class="rt-grid" @mouseleave="grid.rows = grid.cols = 0">
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
    <button
      v-for="a in insertActions"
      :key="a.key"
      type="button"
      class="rt-tb"
      :aria-label="label(a.key)"
      :title="label(a.key)"
      :aria-pressed="a.isActive?.() ?? false"
      :disabled="!can(a.command)"
      @mousedown.prevent
      @click="run(a.command)"
    >
      <v-icon size="17">{{ a.icon }}</v-icon>
    </button>
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

<style scoped>
.rt-toolbar {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  padding: 4px 8px;
  overflow-x: auto;
  scrollbar-width: thin;
}
.rt-tb {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 28px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.rt-tb--menu {
  padding: 0 0 0 4px;
}
.rt-tb:hover:not(:disabled),
.rt-tb[aria-pressed='true'],
.rt-tb[aria-expanded='true'] {
  background: var(--fill);
  color: var(--ink);
}
.rt-tb:disabled {
  color: var(--faint);
  cursor: default;
}
.rt-tb:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.rt-tb--swatch {
  position: relative;
}
.rt-tb__bar {
  position: absolute;
  right: 7px;
  bottom: 4px;
  left: 7px;
  height: 3px;
  border-radius: var(--radius-pill);
}
.rt-tb__bar.is-empty {
  background: var(--line-2);
}
.rt-tb__sep {
  flex: 0 0 1px;
  align-self: stretch;
  margin: 4px;
  background: var(--line);
}
.rt-pop {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-2);
  color: var(--text);
}
.rt-pop--list {
  gap: 0;
  max-height: 320px;
  padding: 4px;
  overflow-y: auto;
}
.rt-pop--form {
  width: 280px;
}
.rt-pop__item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  white-space: nowrap;
}
.rt-pop__item:hover:not(:disabled) {
  background: var(--fill);
}
.rt-pop__item[aria-pressed='true'] {
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}
.rt-pop__item:disabled {
  color: var(--faint);
}
.rt-pop__item--h1,
.rt-pop__item--h2 {
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
}
.rt-pop__item--h3,
.rt-pop__item--h4 {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}
.rt-pop__item--h5,
.rt-pop__item--h6 {
  font-weight: 600;
}
.rt-pop__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.rt-swatches {
  display: grid;
  grid-template-columns: repeat(8, 24px);
  gap: 4px;
}
.rt-swatch {
  width: 24px;
  height: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
.rt-swatch[aria-pressed='true'] {
  outline: 2px solid var(--ink);
  outline-offset: 1px;
}
.rt-grid {
  display: grid;
  grid-template-columns: repeat(8, 16px);
  gap: 4px;
}
.rt-grid__cell {
  width: 16px;
  height: 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}
.rt-grid__cell.is-on {
  border-color: var(--muted);
  background: var(--fill);
}
.rt-grid__size {
  text-align: center;
}
</style>
