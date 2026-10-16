// 工具栏上那几组口令按钮与弹层要用的常量。一条动作只有图标和命令，选中态由 isActive
// 现算；命令是 tiptap 的链式调用，编辑器由调用处交给它（见 useRichTextState）。

import type { ChainedCommands } from '@tiptap/core'

import { t } from '@/i18n'

/** 一条口令：图标 + 一条命令，可以带一个现算的选中态。 */
export interface ToolbarAction {
  key: string
  icon: string
  isActive?: () => boolean
  command: (c: ChainedCommands) => ChainedCommands
}

/** 只有一条命令、没有选中态的那种（表格里的行列操作）。 */
export interface ToolbarCommand {
  key: string
  command: (c: ChainedCommands) => ChainedCommands
}

/** 读编辑器状态的那个函数，就是 useRichTextState 返回的 active。 */
export type ActionActive = (name: string, attrs?: Record<string, unknown>) => boolean

/** 工具栏上一个键的文字，键见 i18n 的 `editor.toolbar`。 */
export const toolbarLabel = (key: string) => t(`editor.toolbar.${key}`)

// 弹层要盖得住全屏的编辑器（TipTapEditor 里那一层是 2450，在弹窗 2400 之上）。
// z-index 属性只收数字，读不到 CSS token，这里是 `--z-menu`（design-system §3.8）在 JS
// 里的唯一一份。
export const MENU_Z = 2500

/** 历史：撤销、重做、清掉全部格式。 */
export const historyActions: ToolbarAction[] = [
  { key: 'undo', icon: 'mdi-undo', command: (c) => c.undo() },
  { key: 'redo', icon: 'mdi-redo', command: (c) => c.redo() },
  { key: 'clear', icon: 'mdi-format-clear', command: (c) => c.unsetAllMarks().clearNodes() },
]

/** 缩进、反缩进落在哪一种列表项上，跟着现在的位置走。 */
const listItemType = (active: ActionActive) => (active('taskItem') ? 'taskItem' : 'listItem')

/**
 * 字符格式、块、插入这三组要读编辑器状态，所以由工具栏拿 `active` 现算一份。
 * 历史那一组不看状态，是上面的常量。
 */
export function createToolbarActions(active: ActionActive): {
  markActions: ToolbarAction[]
  blockActions: ToolbarAction[]
  insertActions: ToolbarAction[]
} {
  const markActions: ToolbarAction[] = [
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
  const blockActions: ToolbarAction[] = [
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
    { key: 'indent', icon: 'mdi-format-indent-increase', command: (c) => c.sinkListItem(listItemType(active)) },
    { key: 'outdent', icon: 'mdi-format-indent-decrease', command: (c) => c.liftListItem(listItemType(active)) },
  ]
  const insertActions: ToolbarAction[] = [
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
  return { markActions, blockActions, insertActions }
}

/** 光标已经在表格里时，表格弹层里的行列操作。 */
export const tableActions: ToolbarCommand[] = [
  { key: 'addRowBefore', command: (c) => c.addRowBefore() },
  { key: 'addRowAfter', command: (c) => c.addRowAfter() },
  { key: 'addColumnBefore', command: (c) => c.addColumnBefore() },
  { key: 'addColumnAfter', command: (c) => c.addColumnAfter() },
  { key: 'deleteRow', command: (c) => c.deleteRow() },
  { key: 'deleteColumn', command: (c) => c.deleteColumn() },
  { key: 'mergeOrSplit', command: (c) => c.mergeOrSplit() },
  { key: 'deleteTable', command: (c) => c.deleteTable() },
]
