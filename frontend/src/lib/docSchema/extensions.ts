// The living document's editor extensions: one list that defines the
// document's schema and how each node reads and writes Markdown.
//
// Part of the document schema module (see ./index.ts): nothing here imports
// the app.

import type { AnyExtension, JSONContent } from '@tiptap/core'
import type { ImageOptions } from '@tiptap/extension-image'
import type { marked } from 'marked'

import { Extension, InputRule, Mark, mergeAttributes, Node } from '@tiptap/core'
import Blockquote from '@tiptap/extension-blockquote'
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import Document from '@tiptap/extension-document'
import Image from '@tiptap/extension-image'
import { BulletList, ListItem, OrderedList, TaskItem, TaskList } from '@tiptap/extension-list'
import { Table, TableCell, TableHeader, TableKit } from '@tiptap/extension-table'
import { Markdown } from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'
import { common, createLowlight } from 'lowlight'

import { App } from './app'
import { docBlocks } from './blocks'
import { Canvas } from './canvas'
import { CommentAnchor } from './commentAnchors'
import { docMarked } from './markdown'
import { suggestableBlocks, suggestionMarks } from './suggestions'

// One lowlight instance (common ≈ 37 languages), shared by every editor.
export const lowlight = createLowlight(common)

// ---- Image: what is drawn is fetched, but the node ATTR keeps the original
// path — markdown serialization reads the attr, so `![](figures/x.png)` is
// written back verbatim, never a URL. Fetched rather than put in `<img src>`
// because the bytes are only given to a request that carries the reader's
// credential, and an `<img>` request carries none.
export interface DocImageOptions {
  /** The URL to draw a markdown src with (an object URL of its bytes, or the
   * src itself when it is already one). Without it the src is drawn as is. */
  loadSrc: ((src: string) => Promise<string>) | null
}

const DocImage = Image.extend<DocImageOptions & ImageOptions>({
  addOptions() {
    return {
      // parent always exists for an .extend()ed extension; `!` keeps the
      // strict production build honest about the non-optional base options.
      ...this.parent!(),
      loadSrc: null,
    }
  },
  addNodeView() {
    const load = this.options.loadSrc
    if (!load || typeof document === 'undefined') return null
    const fixed = this.options.HTMLAttributes
    return ({ node }) => {
      const img = document.createElement('img')
      for (const [key, value] of Object.entries(fixed)) img.setAttribute(key, String(value))
      let wanted = ''
      let drawn = ''
      // Only an object URL made for this view is let go: a src that already
      // was one is somebody else's.
      let made = false
      const release = (url: string, ours: boolean) => {
        if (ours && url.startsWith('blob:')) URL.revokeObjectURL(url)
      }
      const describe = (attrs: Record<string, unknown>) => {
        for (const key of ['alt', 'title'] as const) {
          const value = attrs[key]
          if (typeof value === 'string' && value) img.setAttribute(key, value)
          else img.removeAttribute(key)
        }
      }
      const draw = (src: string) => {
        wanted = src
        if (!src) return
        load(src).then(
          (url) => {
            if (wanted !== src) return release(url, url !== src)
            const [before, beforeMade] = [drawn, made]
            drawn = url
            made = url !== src
            img.src = url
            if (before !== url) release(before, beforeMade)
          },
          () => {
            // Not readable (gone, or not the reader's to see): the alt text stands in.
            if (wanted === src) img.removeAttribute('src')
          }
        )
      }
      describe(node.attrs)
      draw(String(node.attrs.src ?? ''))
      return {
        dom: img,
        update(updated) {
          if (updated.type !== node.type) return false
          describe(updated.attrs)
          const src = String(updated.attrs.src ?? '')
          if (src !== wanted) draw(src)
          return true
        },
        destroy() {
          wanted = ''
          release(drawn, made)
        },
      }
    }
  },
})

// ---- Code block: lowlight highlighting + a data-language attribute on <pre>
// so CSS can show the language corner tag (content: attr(data-language)).
const DocCodeBlock = CodeBlockLowlight.extend({
  addKeyboardShortcuts() {
    return {
      ...this.parent?.(),
      // Tab must INDENT inside a code block, not throw focus out of the
      // editor. Single caret inserts \t; a multi-line selection indents
      // every touched line. Shift-Tab dedents (one \t or up to 2 spaces).
      Tab: () => {
        if (!this.editor.isActive(this.name)) return false
        const { state } = this.editor
        const { from, to } = state.selection
        const multiline = state.doc.textBetween(from, to, '\n').includes('\n')
        if (!multiline) return this.editor.commands.insertContent('\t')
        const text = state.doc.textBetween(from, to, '\n')
        const indented = text
          .split('\n')
          .map((l) => '\t' + l)
          .join('\n')
        return this.editor.commands.insertContentAt({ from, to }, indented)
      },
      'Shift-Tab': () => {
        if (!this.editor.isActive(this.name)) return false
        const { state } = this.editor
        const { from } = state.selection
        const $from = state.doc.resolve(from)
        const lineStart = from - $from.parentOffset
        const blockText = $from.parent.textContent
        // Locate the current line's start inside the block.
        const beforeCaret = blockText.slice(0, $from.parentOffset)
        const relLineStart = beforeCaret.lastIndexOf('\n') + 1
        const absLineStart = lineStart + relLineStart
        const line = blockText.slice(relLineStart)
        const m = line.match(/^(\t| {1,2})/)
        if (!m) return true // nothing to dedent — swallow the key anyway
        return this.editor.commands.deleteRange({
          from: absLineStart,
          to: absLineStart + m[1].length,
        })
      },
    }
  },
  // Every code block marked finds is one. tiptap's own reader takes only a
  // fence that starts the line, so one indented by one to three spaces (still a
  // fence to CommonMark, and common under a list item) was dropped, code and all.
  parseMarkdown: (token, helpers) =>
    helpers.createNode(
      'codeBlock',
      { language: token.lang || null },
      token.text ? [helpers.createTextNode(token.text)] : []
    ),
  renderHTML({ node, HTMLAttributes }) {
    return [
      'pre',
      mergeAttributes(
        this.options.HTMLAttributes,
        HTMLAttributes,
        node.attrs.language ? { 'data-language': node.attrs.language } : {}
      ),
      [
        'code',
        {
          class: node.attrs.language ? this.options.languageClassPrefix + node.attrs.language : null,
        },
        0,
      ],
    ]
  },
})

// ---- Typing `[text](url)` turns into a real link as you close the paren.
// This parses OUR structured syntax (markdown), not natural language.
const LINK_INPUT_RE = /\[([^\][]+)\]\((\S+?)\)$/
const MarkdownLinkInput = Extension.create({
  name: 'markdownLinkInput',
  addInputRules() {
    return [
      new InputRule({
        find: LINK_INPUT_RE,
        handler: ({ state, range, match }) => {
          const linkMark = state.schema.marks.link
          if (!linkMark) return
          const [, text, href] = match
          state.tr.replaceWith(range.from, range.to, state.schema.text(text, [linkMark.create({ href })]))
          // Don't carry the link mark into whatever is typed next.
          state.tr.removeStoredMark(linkMark)
        },
      }),
    ]
  },
})

// ---- Highlight: written `<mark>…</mark>`, which every Markdown renderer
// shows. Not `==…==`: that is no Markdown standard, so the text would read as
// stray equals signs everywhere else the document is read, and a prose
// comparison like `a == b … c == d` would come back highlighted.
const DocHighlight = Mark.create({
  name: 'highlight',
  parseHTML() {
    return [{ tag: 'mark' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['mark', mergeAttributes(HTMLAttributes), 0]
  },
  renderMarkdown: (node, helpers) => `<mark>${helpers.renderChildren(node)}</mark>`,
  // Read as Markdown, not handed to the HTML parser: what is inside keeps its
  // formatting (`<mark>**加粗**</mark>`), which the HTML parser would take as
  // literal asterisks.
  parseMarkdown: (token, helpers) => helpers.applyMark('highlight', helpers.parseInline(token.tokens || [])),
  markdownTokenizer: {
    name: 'highlight',
    level: 'inline',
    start: (src) => src.indexOf('<mark>'),
    tokenize(src, _tokens, helpers) {
      const match = /^<mark>([\s\S]+?)<\/mark>/.exec(src)
      if (!match) return undefined
      return { type: 'highlight', raw: match[0], text: match[1], tokens: helpers.inlineTokens(match[1]) }
    },
  },
  addKeyboardShortcuts() {
    return { 'Mod-Shift-h': () => this.editor.commands.toggleMark(this.name) }
  },
})

export interface DocExtensionsOptions {
  /** The editor holds its own content instead of a shared document (a form
   * field such as a task description): it keeps a local undo history and the
   * empty paragraph after a closing table or list, which only a shared document
   * has to give up. */
  standalone?: boolean
  /** What an image is drawn with (see DocImageOptions.loadSrc); without it
   * the src is drawn as written. */
  loadImage?: (src: string) => Promise<string>
}

// Keep standalone Markdown comments as invisible document nodes. Dropping them
// would lose source annotations from the document.
const DocComment = Node.create({
  name: 'docComment',
  group: 'block',
  atom: true,
  selectable: false,
  addAttributes() {
    return { source: { default: '', rendered: false } }
  },
  parseHTML() {
    return [
      { tag: 'div[data-doc-comment]', getAttrs: (element) => ({ source: element.getAttribute('data-doc-comment') }) },
    ]
  },
  renderHTML({ node }) {
    return ['div', { 'data-doc-comment': node.attrs.source, hidden: '', 'aria-hidden': 'true' }]
  },
  parseMarkdown: (token, helpers) => helpers.createNode('docComment', { source: token.text }),
  renderMarkdown: (node) => node.attrs?.source ?? '',
  markdownTokenizer: {
    name: 'docComment',
    level: 'block',
    start: (source) => source.search(/^ {0,3}<!--/m),
    tokenize(source) {
      const match = source.match(/^ {0,3}<!--[\s\S]*?-->[ \t]*(?:\n|$)/)
      if (!match) return undefined
      return { type: 'docComment', raw: match[0], text: match[0].trimEnd() }
    },
  },
})

// GFM splits a table row on every `|` not escaped as `\|` — inside a code
// span too, and it takes the backslash back off before the cell is read. The
// upstream renderer writes a cell's text as is, so a cell saying `a|b` (in
// code or not) came back as two cells. Every `|` a cell renders is written
// `\|`; text backslashes are already escaped by then, so nothing else reads
// as an escape.
const DocTable = Table.extend({
  renderMarkdown(node, helpers, context) {
    const renderChildren: typeof helpers.renderChildren = (nodes, separator) =>
      helpers.renderChildren(nodes, separator).replace(/\|/g, '\\|')
    return Table.config.renderMarkdown!(node, { ...helpers, renderChildren }, context)
  },
})

// An item can open with an empty line and hold only a nested list (an editor
// makes one by indenting the item below an empty bullet). Written as "- "
// with its trailing space, marked reads that line as a paragraph saying "- "
// and the nested list as a list of its own; written as a bare "-", it reads
// the nested list as the item's, and an item read without its leading
// paragraph gets the empty one back. Otherwise the Markdown of such a list
// does not read back as the list, and no edit near it can be placed.
const DocListItem = ListItem.extend({
  parseMarkdown(token, helpers) {
    const item = ListItem.config.parseMarkdown!(token, helpers) as JSONContent
    if (item?.type === 'listItem' && item.content?.[0]?.type !== 'paragraph') {
      return { ...item, content: [{ type: 'paragraph', content: [] }, ...(item.content ?? [])] }
    }
    return item
  },
  renderMarkdown(node, helpers, context) {
    let out: string
    if (context.parentType !== 'orderedList' || context.meta?.parentAttrs?.type) {
      out = ListItem.config.renderMarkdown!(node, helpers, context)
    } else {
      // CommonMark nests beneath the content column: "1. " needs three spaces,
      // "10. " needs four. The upstream helper always uses two and flattens it.
      const start = Number(context.meta?.parentAttrs?.start ?? 1)
      const width = `${start + (context.index ?? 0)}. `.length
      out = ListItem.config.renderMarkdown!(node, { ...helpers, indent: (text) => ' '.repeat(width) + text }, context)
    }
    const [first, ...rest] = node.content ?? []
    const opensEmpty = first?.type === 'paragraph' && !first.content?.length && rest.length > 0
    return opensEmpty ? out.replace(/^([^\n]*?)[ \t]+\n/, '$1\n') : out
  },
})

// An ordered list is read by marked, the same way a bullet list is. tiptap's
// own ordered-list reader takes an item's content as starting two columns in
// where `1. ` takes three, so a fenced code block under a step lost its first
// space, and one after a second paragraph was dropped.
//
// Its items are read by the list item, as a bullet list's are. tiptap's ordered
// list reads them itself, and takes a tight item's text token as one plain text
// node: `1. **标题** {✓ 状态}` kept its asterisks and tag as literal characters,
// and saving it wrote them back escaped.
const DocOrderedList = OrderedList.extend({
  markdownTokenizer: null as never,
  parseMarkdown: (token, helpers) => {
    if (token.type !== 'list' || !token.ordered) return []
    const start = Number(token.start || 1)
    const attrs: Record<string, unknown> = {}
    if (start !== 1) attrs.start = start
    if (token.typeMarker) attrs.type = token.typeMarker
    return {
      type: 'orderedList',
      ...(Object.keys(attrs).length ? { attrs } : {}),
      content: token.items ? helpers.parseChildren(token.items) : [],
    }
  },
})

/** The full extension list for the living-doc editor (and its tests). */
export function docExtensions(opts: DocExtensionsOptions = {}): AnyExtension[] {
  const extensions: AnyExtension[] = [
    StarterKit.configure({
      // Added below as they are, so that a shared document can let a whole
      // block in them be suggested (suggestableBlocks).
      document: false,
      blockquote: false,
      bulletList: false,
      // Replaced by the lowlight-highlighted code block below.
      codeBlock: false,
      // A shared document is edited collaboratively, and the collaboration
      // extension brings its own undo: one that takes back only your own
      // changes, not whatever a teammate typed in between.
      undoRedo: opts.standalone ? undefined : false,
      // An editor that appends an empty paragraph after a closing table or
      // list changes the shared document just by opening it: every reader would
      // record a version, and two readers would append two paragraphs.
      trailingNode: opts.standalone ? undefined : false,
      listItem: false,
      orderedList: false,
      link: {
        // No click-through plugin: in edit mode a plain click just places the
        // caret (⌘-click opens via DocPanel's delegated handler); in read
        // mode the rendered <a target=_blank> navigates natively.
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
        HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' },
      },
    }),
    // The cast: @tiptap/markdown types this option as the marked MODULE, but
    // reads only Lexer/defaults/use/lexer/setOptions off it — all present on an
    // instance, which is what its own README passes. `getDefaults` is the one
    // module-only member, and nothing in the package calls it.
    Markdown.configure({ marked: docMarked as unknown as typeof marked }),
    DocComment,
    Document,
    Blockquote,
    BulletList,
    TableKit.configure({ table: false, tableCell: false, tableHeader: false }),
    DocTable.configure({ resizable: false }),
    TableCell,
    TableHeader,
    DocListItem,
    DocOrderedList,
    TaskList,
    TaskItem.configure({ nested: true }),
    DocImage.configure({ loadSrc: opts.loadImage ?? null }),
    DocCodeBlock.configure({ lowlight }),
    MarkdownLinkInput,
    DocHighlight,
    ...docBlocks,
    App,
    // A form field holds text alone: a board belongs to a document.
    ...(opts.standalone ? [] : [Canvas]),
    // Suggested changes belong to the shared document; a form field is written
    // by one person and has nobody to suggest to.
    ...(opts.standalone ? [] : suggestionMarks),
    // A form field has no comments.
    ...(opts.standalone ? [] : [CommentAnchor]),
  ]
  return opts.standalone ? extensions : extensions.map(suggestableBlocks)
}
