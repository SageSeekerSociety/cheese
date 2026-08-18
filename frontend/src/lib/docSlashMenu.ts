// Notion 式 slash 菜单：在块首打 "/" 弹出块类型菜单，选一个就地把这个块转成那
// 个类型（保留原文）。
//
// 它住在 lib/ 而不是 PanelDoc 里，因为它是**内容**而不是布局：一张块类型表、一个
// 按关键词（英文 + 拼音全拼 + 首字母）筛选的函数、和一个 tiptap 扩展。三样都不碰
// 文档面板的 DOM，也都不该为了读一行菜单文案去翻两千行的编辑器组件。筛选那一段尤
// 其值得单独测：拼音关键词打错了不会报错，只会「打了 bt 出不来标题」。
//
// 建在 @tiptap/suggestion 上：那个 "/" 是**我们自己的结构化触发符**，插件按位置
// 匹配，从不解析自然语言（军规 4）。它不新增任何 node 或 mark，所以共享的
// round-trip schema 一个字没动。
import type { ChainedCommands } from '@tiptap/core'
import type { SuggestionProps } from '@tiptap/suggestion'

import { Extension } from '@tiptap/core'
import { PluginKey } from '@tiptap/pm/state'
import { Suggestion } from '@tiptap/suggestion'

export interface SlashItem {
  key: string
  label: string
  icon: string
  hint: string
  /** Filter keywords: english names + pinyin (full + initials). */
  keywords: string[]
  /** Applied AFTER the "/query" token is deleted; must keep block text. */
  run: (chain: ChainedCommands) => ChainedCommands
}

// clearNodes() first: it lifts list items / quotes and normalizes the current
// block back to a paragraph, so every conversion starts from the same shape —
// that's what makes 标题↔正文↔列表↔引用 all interconvertible. Text survives;
// 代码块 takes the whole block's text as its code content.
export const SLASH_ITEMS: SlashItem[] = [
  {
    key: 'text',
    label: '正文',
    icon: 'mdi-format-paragraph',
    hint: 'text',
    keywords: ['text', 'paragraph', 'p', 'zw', 'zhengwen'],
    run: (c) => c.clearNodes(),
  },
  {
    key: 'h1',
    label: '标题 1',
    icon: 'mdi-format-header-1',
    hint: 'h1',
    keywords: ['h1', 'heading1', 'title', 'bt1', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 1 }),
  },
  {
    key: 'h2',
    label: '标题 2',
    icon: 'mdi-format-header-2',
    hint: 'h2',
    keywords: ['h2', 'heading2', 'bt2', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 2 }),
  },
  {
    key: 'h3',
    label: '标题 3',
    icon: 'mdi-format-header-3',
    hint: 'h3',
    keywords: ['h3', 'heading3', 'bt3', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 3 }),
  },
  {
    key: 'bullet',
    label: '无序列表',
    icon: 'mdi-format-list-bulleted',
    hint: 'list',
    keywords: ['ul', 'list', 'bullet', 'wxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleBulletList(),
  },
  {
    key: 'ordered',
    label: '有序列表',
    icon: 'mdi-format-list-numbered',
    hint: '1.',
    keywords: ['ol', 'list', 'ordered', 'number', 'yxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleOrderedList(),
  },
  {
    key: 'task',
    label: '任务列表',
    icon: 'mdi-format-list-checks',
    hint: 'todo',
    keywords: ['todo', 'task', 'checkbox', 'list', 'rwlb', 'renwu', 'liebiao'],
    run: (c) => c.clearNodes().toggleTaskList(),
  },
  {
    key: 'code',
    label: '代码块',
    icon: 'mdi-code-tags',
    hint: 'code',
    keywords: ['code', 'codeblock', 'pre', 'dmk', 'daima'],
    run: (c) => c.clearNodes().setNode('codeBlock'),
  },
  {
    key: 'quote',
    label: '引用',
    icon: 'mdi-format-quote-close',
    hint: 'quote',
    keywords: ['quote', 'blockquote', 'yy', 'yinyong'],
    run: (c) => c.clearNodes().toggleBlockquote(),
  },
  {
    key: 'table',
    label: '表格',
    icon: 'mdi-table',
    hint: 'table',
    keywords: ['table', 'bg', 'biaoge'],
    run: (c) => c.insertTable({ rows: 2, cols: 3, withHeaderRow: true }),
  },
  {
    key: 'hr',
    label: '分割线',
    icon: 'mdi-minus',
    hint: '---',
    keywords: ['hr', 'divider', 'line', 'fgx', 'fengexian'],
    run: (c) => c.setHorizontalRule(),
  },
]

export function filterSlashItems(query: string): SlashItem[] {
  const q = query.toLowerCase().trim()
  if (!q) return SLASH_ITEMS
  return SLASH_ITEMS.filter((it) => it.label.includes(q) || it.keywords.some((k) => k.includes(q)))
}

export interface SlashMenuHandlers {
  onStart: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onUpdate: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onExit: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onKeyDown: (props: { event: KeyboardEvent }) => boolean
}

export const slashPluginKey = new PluginKey('cheeseSlashMenu')

/** The tiptap extension. The host supplies the four render hooks, because where
 *  a floating menu goes on screen is the panel's business, not this table's. */
export function createSlashCommands(handlers: SlashMenuHandlers) {
  return Extension.create({
    name: 'cheeseSlashCommands',
    addProseMirrorPlugins() {
      return [
        Suggestion<SlashItem, SlashItem>({
          editor: this.editor,
          pluginKey: slashPluginKey,
          char: '/',
          // 空段落或行首: the trigger only arms at the head of a text block —
          // mid-sentence "/" (dates, paths) never opens the menu.
          startOfLine: true,
          allowedPrefixes: null,
          items: ({ query }) => filterSlashItems(query),
          allow: ({ state, range }) => {
            // Never inside code blocks ("/" is code) or table cells (block-type
            // conversions there would produce markdown a GFM table can't hold —
            // the round-trip guard would flag the doc as lossy).
            const $from = state.doc.resolve(range.from)
            for (let d = $from.depth; d > 0; d--) {
              const name = $from.node(d).type.name
              if (name === 'codeBlock' || name === 'tableCell' || name === 'tableHeader') {
                return false
              }
            }
            return true
          },
          command: ({ editor: ed, range, props: item }) => {
            // One chain = one undo step: drop the "/query" token, then convert.
            item.run(ed.chain().focus().deleteRange(range)).run()
          },
          render: () => handlers,
        }),
      ]
    },
  })
}
