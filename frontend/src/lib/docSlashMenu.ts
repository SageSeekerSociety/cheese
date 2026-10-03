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
import type { ChainedCommands, Editor } from '@tiptap/core'
import type { Node as PMNode, Schema } from '@tiptap/pm/model'
import type { SuggestionProps } from '@tiptap/suggestion'

import { Extension } from '@tiptap/core'
import { PluginKey, TextSelection } from '@tiptap/pm/state'
import { Suggestion } from '@tiptap/suggestion'

import { emptyItem, FIELD_NODES } from './docSchema/blocks'

import { t } from '@/i18n'

export interface SlashItem {
  key: string
  label: string
  icon: string
  hint: string
  /** Filter keywords: english names + pinyin (full + initials). */
  keywords: string[]
  /** Applied AFTER the "/query" token is deleted; must keep block text. */
  run: (chain: ChainedCommands) => ChainedCommands
  /** Goes inside a one-line field (a timeline item, a stat card) too. */
  inline?: boolean
  /** Inserts something new rather than turning the block into another kind. */
  insert?: boolean
  /** Needs more than the chain: the host opens what it asks for. */
  action?: 'status'
}

/** Put a new block where the caret is: in place of the empty paragraph the
 *  "/" was typed in, or after the paragraph that has text. The caret goes
 *  `inner` positions into the new block. */
function insertBlock(make: (schema: Schema) => PMNode, inner: number | ((node: PMNode) => number)) {
  return (c: ChainedCommands) =>
    c.command(({ tr, state, dispatch }) => {
      const { $from } = tr.selection
      const node = make(state.schema)
      const empty = $from.parent.type.name === 'paragraph' && $from.parent.content.size === 0
      const at = empty ? $from.before() : $from.after()
      if (empty) tr.replaceWith(at, $from.after(), node)
      else tr.insert(at, node)
      const into = typeof inner === 'number' ? inner : inner(node)
      if (dispatch) tr.setSelection(TextSelection.near(tr.doc.resolve(Math.min(at + into, tr.doc.content.size))))
      return true
    })
}

// clearNodes() first: it lifts list items / quotes and normalizes the current
// block back to a paragraph, so every conversion starts from the same shape —
// that's what makes 标题↔正文↔列表↔引用 all interconvertible. Text survives;
// 代码块 takes the whole block's text as its code content.
export const SLASH_ITEMS: SlashItem[] = [
  {
    key: 'text',
    get label() {
      return t('work.room.doc.slash.text')
    },
    icon: 'mdi-format-paragraph',
    hint: 'text',
    keywords: ['text', 'paragraph', 'p', 'zw', 'zhengwen'],
    run: (c) => c.clearNodes(),
  },
  {
    key: 'h1',
    get label() {
      return t('work.room.doc.slash.h1')
    },
    icon: 'mdi-format-header-1',
    hint: 'h1',
    keywords: ['h1', 'heading1', 'title', 'bt1', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 1 }),
  },
  {
    key: 'h2',
    get label() {
      return t('work.room.doc.slash.h2')
    },
    icon: 'mdi-format-header-2',
    hint: 'h2',
    keywords: ['h2', 'heading2', 'bt2', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 2 }),
  },
  {
    key: 'h3',
    get label() {
      return t('work.room.doc.slash.h3')
    },
    icon: 'mdi-format-header-3',
    hint: 'h3',
    keywords: ['h3', 'heading3', 'bt3', 'biaoti'],
    run: (c) => c.clearNodes().setNode('heading', { level: 3 }),
  },
  {
    key: 'bullet',
    get label() {
      return t('work.room.doc.slash.bullet')
    },
    icon: 'mdi-format-list-bulleted',
    hint: 'list',
    keywords: ['ul', 'list', 'bullet', 'wxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleBulletList(),
  },
  {
    key: 'ordered',
    get label() {
      return t('work.room.doc.slash.ordered')
    },
    icon: 'mdi-format-list-numbered',
    hint: '1.',
    keywords: ['ol', 'list', 'ordered', 'number', 'yxlb', 'liebiao'],
    run: (c) => c.clearNodes().toggleOrderedList(),
  },
  {
    key: 'task',
    get label() {
      return t('work.room.doc.slash.task')
    },
    icon: 'mdi-format-list-checks',
    hint: 'todo',
    keywords: ['todo', 'task', 'checkbox', 'list', 'rwlb', 'renwu', 'liebiao'],
    run: (c) => c.clearNodes().toggleTaskList(),
  },
  {
    key: 'code',
    get label() {
      return t('work.room.doc.slash.code')
    },
    icon: 'mdi-code-tags',
    hint: 'code',
    keywords: ['code', 'codeblock', 'pre', 'dmk', 'daima'],
    run: (c) => c.clearNodes().setNode('codeBlock'),
  },
  {
    key: 'quote',
    get label() {
      return t('work.room.doc.slash.quote')
    },
    icon: 'mdi-format-quote-close',
    hint: 'quote',
    keywords: ['quote', 'blockquote', 'yy', 'yinyong'],
    run: (c) => c.clearNodes().toggleBlockquote(),
  },
  {
    key: 'table',
    get label() {
      return t('work.room.doc.slash.table')
    },
    icon: 'mdi-table',
    hint: 'table',
    keywords: ['table', 'bg', 'biaoge'],
    run: (c) => c.insertTable({ rows: 2, cols: 3, withHeaderRow: true }),
  },
  {
    key: 'hr',
    get label() {
      return t('work.room.doc.slash.hr')
    },
    icon: 'mdi-minus',
    hint: '---',
    keywords: ['hr', 'divider', 'line', 'fgx', 'fengexian'],
    insert: true,
    run: (c) => c.setHorizontalRule(),
  },
  {
    key: 'callout',
    get label() {
      return t('work.room.doc.slash.callout')
    },
    icon: 'mdi-alert-box-outline',
    hint: '[!]',
    keywords: ['callout', 'note', 'alert', 'tsk', 'tishikuang'],
    run: (c) => c.clearNodes().wrapIn('callout', { kind: 'IMPORTANT' }),
  },
  {
    key: 'timeline',
    get label() {
      return t('work.room.doc.slash.timeline')
    },
    icon: 'mdi-timeline-outline',
    hint: ':::',
    keywords: ['timeline', 'steps', 'sjx', 'shijianxian'],
    insert: true,
    run: insertBlock((schema) => schema.nodes.timeline.create(null, emptyItem(schema, 'timelineItem')), 3),
  },
  {
    key: 'stats',
    get label() {
      return t('work.room.doc.slash.stats')
    },
    icon: 'mdi-card-text-outline',
    hint: ':::',
    keywords: ['stats', 'metrics', 'kpi', 'zbk', 'zhibiaoka'],
    insert: true,
    run: insertBlock(
      (schema) => schema.nodes.stats.create(null, [emptyItem(schema, 'statItem'), emptyItem(schema, 'statItem')]),
      3
    ),
  },
  {
    key: 'columns',
    get label() {
      return t('work.room.doc.slash.columns')
    },
    icon: 'mdi-view-column-outline',
    hint: '::::',
    keywords: ['columns', 'side', 'fl', 'fenlan'],
    insert: true,
    run: insertBlock((schema) => {
      const column = () => schema.nodes.column.create(null, schema.nodes.paragraph.create())
      return schema.nodes.columns.create(null, [column(), column()])
    }, 3),
  },
  {
    key: 'details',
    get label() {
      return t('work.room.doc.slash.details')
    },
    icon: 'mdi-chevron-right-box-outline',
    hint: 'details',
    keywords: ['details', 'fold', 'collapse', 'zd', 'zhedie'],
    insert: true,
    run: insertBlock(
      (schema) =>
        schema.nodes.details.create(null, [
          schema.nodes.detailsSummary.create(),
          schema.nodes.detailsContent.create(null, schema.nodes.paragraph.create()),
        ]),
      2
    ),
  },
  {
    key: 'math',
    get label() {
      return t('work.room.doc.slash.math')
    },
    icon: 'mdi-sigma',
    hint: '$$',
    keywords: ['math', 'formula', 'latex', 'equation', 'gs', 'gongshi'],
    insert: true,
    run: insertBlock((schema) => schema.nodes.mathBlock.create({ latex: '' }), 1),
  },
  {
    key: 'chart',
    get label() {
      return t('work.room.doc.slash.chart')
    },
    icon: 'mdi-chart-bar',
    hint: 'chart',
    keywords: ['chart', 'graph', 'bar', 'line', 'pie', 'tb', 'tubiao'],
    insert: true,
    // The caret goes into the first cell under the header.
    run: insertBlock(
      (schema) => {
        const cell = (type: 'tableHeader' | 'tableCell', text = '') =>
          schema.nodes[type].create(null, schema.nodes.paragraph.create(null, text ? schema.text(text) : null))
        const row = (cells: PMNode[]) => schema.nodes.tableRow.create(null, cells)
        return schema.nodes.chart.create({ kind: 'bar' }, [
          schema.nodes.table.create(null, [
            row([
              cell('tableHeader', t('work.room.doc.blocks.chartCategory')),
              cell('tableHeader', t('work.room.doc.blocks.chartValue')),
            ]),
            row([cell('tableCell'), cell('tableCell')]),
            row([cell('tableCell'), cell('tableCell')]),
          ]),
        ])
      },
      (chart) => 2 + (chart.firstChild?.firstChild?.nodeSize ?? 0) + 3
    ),
  },
  {
    key: 'diagram',
    get label() {
      return t('work.room.doc.slash.diagram')
    },
    icon: 'mdi-sitemap-outline',
    hint: 'mermaid',
    keywords: ['mermaid', 'diagram', 'flowchart', 'flow', 'sequence', 'lct', 'liuchengtu'],
    insert: true,
    run: insertBlock((schema) => schema.nodes.codeBlock.create({ language: 'mermaid' }), 1),
  },
  {
    key: 'status',
    get label() {
      return t('work.room.doc.slash.status')
    },
    icon: 'mdi-check-circle-outline',
    hint: '{✓}',
    keywords: ['status', 'tag', 'chip', 'ztbq', 'zhuangtai', 'biaoqian'],
    inline: true,
    insert: true,
    action: 'status',
    run: (c) => c,
  },
]

/** 把已有的一块换成别的块（浮条上的「正文 ▾」、行首的手柄）：同一张表，去掉插入新
 *  东西的那几项（表格、分隔线）。 */
export const BLOCK_ITEMS: SlashItem[] = SLASH_ITEMS.filter((item) => !item.insert && item.key !== 'table')

/** 这一块在表里是哪一项；对不上的（比如表格）算正文。 */
export function blockKeyOf(node: PMNode | null | undefined): string {
  switch (node?.type.name) {
    case 'heading':
      return `h${node.attrs.level as number}`
    case 'bulletList':
      return 'bullet'
    case 'orderedList':
      return 'ordered'
    case 'taskList':
      return 'task'
    case 'codeBlock':
      return 'code'
    case 'blockquote':
      return 'quote'
    case 'callout':
      return 'callout'
    default:
      return 'text'
  }
}

/** The items for this query; in a one-line field only the inline ones. */
export function filterSlashItems(query: string, inField = false): SlashItem[] {
  const q = query.toLowerCase().trim()
  const items = inField ? SLASH_ITEMS.filter((it) => it.inline) : SLASH_ITEMS
  if (!q) return items
  return items.filter((it) => it.label.includes(q) || it.keywords.some((k) => k.includes(q)))
}

export interface SlashMenuHandlers {
  onStart: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onUpdate: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onExit: (props: SuggestionProps<SlashItem, SlashItem>) => void
  onKeyDown: (props: { event: KeyboardEvent }) => boolean
  /** An item that needs more than a chain (a new status tag asks for its words). */
  onAction?: (action: NonNullable<SlashItem['action']>, editor: Editor) => void
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
          // At the head of a text block, or after a space: "/" inside a word
          // (dates, paths) never opens the menu. After a space only the
          // inline items are offered, since turning the whole paragraph into
          // a heading from the middle of a sentence is never what was meant.
          startOfLine: false,
          allowedPrefixes: [' '],
          items: ({ query, editor }) => {
            const { $from } = editor.state.selection
            const atHead = $from.parentOffset === query.length + 1
            return filterSlashItems(query, !atHead || FIELD_NODES.has($from.parent.type.name))
          },
          allow: ({ state, range }) => {
            // Never inside code blocks ("/" is code) or table cells (block-type
            // conversions there would produce markdown a GFM table can't hold).
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
            if (item.action) handlers.onAction?.(item.action, ed)
          },
          render: () => handlers,
        }),
      ]
    },
  })
}
