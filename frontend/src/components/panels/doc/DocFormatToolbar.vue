<script setup lang="ts">
import type { ChainedCommands, Editor } from '@tiptap/core'

import { computed, ref, toRaw, watch } from 'vue'

import { t } from '@/i18n'

/** 文档这一排按钮之外，宿主再往后接的按钮（题目详情等处的插图、表格）。 */
export interface ExtraFormatAction {
  key: string
  icon: string
  label: string
  /** 不给就一直在；给了，只在它说在的时候出现（光标在表格里才有的那几个）。 */
  visible?: (editor: Editor) => boolean
  isActive?: (editor: Editor) => boolean
  /** 编辑器命令；要先做别的事（选文件）就给 `run`。 */
  command?: (c: ChainedCommands) => ChainedCommands
  run?: () => void
  busy?: boolean
}

const props = defineProps<{
  editor: Editor | null
  disabled: boolean
  disabledReason: string
  extra?: ExtraFormatAction[]
}>()
const revision = ref(0)
const toolbar = ref<HTMLElement | null>(null)
const focused = ref(0)
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
const actions = computed(() => {
  void revision.value
  const editor = toRaw(props.editor)
  const items = [
    {
      key: 'bold',
      icon: 'mdi-format-bold',
      active: editor?.isActive('bold'),
      command: (c: ChainedCommands) => c.toggleBold(),
    },
    {
      key: 'italic',
      icon: 'mdi-format-italic',
      active: editor?.isActive('italic'),
      command: (c: ChainedCommands) => c.toggleItalic(),
    },
    {
      key: 'strike',
      icon: 'mdi-format-strikethrough',
      active: editor?.isActive('strike'),
      command: (c: ChainedCommands) => c.toggleStrike(),
    },
    {
      key: 'code',
      icon: 'mdi-code-tags',
      active: editor?.isActive('code'),
      command: (c: ChainedCommands) => c.toggleCode(),
    },
    {
      key: 'h1',
      icon: 'mdi-format-header-1',
      active: editor?.isActive('heading', { level: 1 }),
      command: (c: ChainedCommands) => c.toggleHeading({ level: 1 }),
    },
    {
      key: 'h2',
      icon: 'mdi-format-header-2',
      active: editor?.isActive('heading', { level: 2 }),
      command: (c: ChainedCommands) => c.toggleHeading({ level: 2 }),
    },
    {
      key: 'bullet',
      icon: 'mdi-format-list-bulleted',
      active: editor?.isActive('bulletList'),
      command: (c: ChainedCommands) => c.toggleBulletList(),
    },
    {
      key: 'ordered',
      icon: 'mdi-format-list-numbered',
      active: editor?.isActive('orderedList'),
      command: (c: ChainedCommands) => c.toggleOrderedList(),
    },
    {
      key: 'task',
      icon: 'mdi-format-list-checks',
      active: editor?.isActive('taskList'),
      command: (c: ChainedCommands) => c.toggleTaskList(),
    },
    {
      key: 'quote',
      icon: 'mdi-format-quote-close',
      active: editor?.isActive('blockquote'),
      command: (c: ChainedCommands) => c.toggleBlockquote(),
    },
  ].map((item) => ({ ...item, label: label(item.key), run: undefined as (() => void) | undefined, busy: false }))
  const extra = (props.extra ?? [])
    .filter((item) => !editor || !item.visible || item.visible(editor))
    .map((item) => ({
      key: item.key,
      icon: item.icon,
      label: item.label,
      active: editor ? item.isActive?.(editor) : false,
      command: item.command,
      run: item.run,
      busy: !!item.busy,
    }))
  return [...items, ...extra].map((item) => ({
    ...item,
    disabled:
      props.disabled ||
      item.busy ||
      !editor?.isEditable ||
      (item.command ? !item.command(editor.can().chain()).run() : false),
  }))
})
function moveFocus(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    toRaw(props.editor)?.commands.focus(undefined, { scrollIntoView: false })
    return
  }
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const count = actions.value.length
  focused.value =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? count - 1
        : (focused.value + (event.key === 'ArrowRight' ? 1 : -1) + count) % count
  const container = toolbar.value
  const button = container?.querySelectorAll<HTMLButtonElement>('button')[focused.value]
  if (!container || !button) return
  button.focus({ preventScroll: true })
  const viewport = container.getBoundingClientRect()
  const target = button.getBoundingClientRect()
  // Reveal the control without scrolling the document or its outer panels.
  if (target.left < viewport.left) container.scrollLeft += target.left - viewport.left
  else if (target.right > viewport.right) container.scrollLeft += target.right - viewport.right
}
function execute(index: number) {
  const action = actions.value[index]
  const editor = toRaw(props.editor)
  if (!action || action.disabled || !editor?.isEditable) return
  if (action.run) action.run()
  else action.command?.(editor.chain().focus()).run()
}
function label(key: string) {
  return t(`work.room.doc.format.${key}`)
}
</script>

<template>
  <div
    ref="toolbar"
    class="doc-format"
    role="toolbar"
    :aria-label="label('label')"
    @mousedown.prevent
    @keydown="moveFocus"
  >
    <button
      v-for="(action, index) in actions"
      :key="action.key"
      type="button"
      :tabindex="index === focused ? 0 : -1"
      :aria-pressed="!!action.active"
      :aria-disabled="action.disabled"
      :aria-label="action.label"
      :title="action.disabled && !action.busy ? disabledReason || label('unavailable') : action.label"
      @focus="focused = index"
      @click="execute(index)"
    >
      <v-icon size="17">{{ action.icon }}</v-icon>
    </button>
  </div>
</template>

<style scoped>
.doc-format {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 2px;
  overflow-x: auto;
  min-width: 0;
  padding: 6px 8px;
}
.doc-format button {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.doc-format button:hover:not([aria-disabled='true']) {
  background: var(--fill);
  color: var(--ink);
}
.doc-format button[aria-pressed='true'] {
  background: var(--fill);
  color: var(--ink);
}
.doc-format button[aria-disabled='true'] {
  color: var(--faint);
  cursor: default;
}
.doc-format button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
</style>
