// The living document's editor extensions: one list that defines the
// document's schema and how each node reads and writes Markdown.
//
// Part of the document schema module (see ./index.ts): nothing here imports
// the app.

import type { AnyExtension } from '@tiptap/core'
import type { ImageOptions } from '@tiptap/extension-image'
import type { marked } from 'marked'

import { Extension, InputRule, Mark, mergeAttributes, Node } from '@tiptap/core'
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import Image from '@tiptap/extension-image'
import { ListItem, TaskItem, TaskList } from '@tiptap/extension-list'
import { TableKit } from '@tiptap/extension-table'
import { Markdown } from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'
import { common, createLowlight } from 'lowlight'

import { docBlocks } from './blocks'
import { CommentAnchor } from './commentAnchors'
import { docMarked } from './markdown'
import { suggestionMarks } from './suggestions'

// One lowlight instance (common ≈ 37 languages), shared by every editor.
const lowlight = createLowlight(common)

// ---- Image: display resolves workspace-relative paths to the raw-file API,
// but the node ATTR keeps the original path — markdown serialization reads the
// attr, so `![](uploads/x.png)` is written back verbatim, never a localhost URL.
export interface DocImageOptions {
  /** Rewrites a markdown src for DISPLAY only (e.g. uploads/x.png → /api/...). */
  resolveSrc: (src: string) => string
}

const DocImage = Image.extend<DocImageOptions & ImageOptions>({
  addOptions() {
    return {
      // parent always exists for an .extend()ed extension; `!` keeps the
      // strict production build honest about the non-optional base options.
      ...this.parent!(),
      resolveSrc: (src: string) => src,
    }
  },
  renderHTML({ HTMLAttributes }) {
    const attrs: Record<string, unknown> = { ...HTMLAttributes }
    if (typeof attrs.src === 'string' && attrs.src) {
      attrs.src = this.options.resolveSrc(attrs.src)
    }
    return ['img', mergeAttributes(this.options.HTMLAttributes, attrs)]
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
  /** Display-time image src resolver (defaults to identity). */
  resolveImageSrc?: (src: string) => string
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

const DocListItem = ListItem.extend({
  renderMarkdown(node, helpers, context) {
    if (context.parentType !== 'orderedList' || context.meta?.parentAttrs?.type) {
      return ListItem.config.renderMarkdown!(node, helpers, context)
    }
    // CommonMark nests beneath the content column: "1. " needs three spaces,
    // "10. " needs four. The upstream helper always uses two and flattens it.
    const start = Number(context.meta?.parentAttrs?.start ?? 1)
    const width = `${start + (context.index ?? 0)}. `.length
    return ListItem.config.renderMarkdown!(node, { ...helpers, indent: (text) => ' '.repeat(width) + text }, context)
  },
})

/** The full extension list for the living-doc editor (and its tests). */
export function docExtensions(opts: DocExtensionsOptions = {}): AnyExtension[] {
  return [
    StarterKit.configure({
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
    TableKit.configure({ table: { resizable: false } }),
    DocListItem,
    TaskList,
    TaskItem.configure({ nested: true }),
    DocImage.configure({
      resolveSrc: opts.resolveImageSrc ?? ((s: string) => s),
    }),
    DocCodeBlock.configure({ lowlight }),
    MarkdownLinkInput,
    DocHighlight,
    ...docBlocks,
    // Suggested changes belong to the shared document; a form field is written
    // by one person and has nobody to suggest to.
    ...(opts.standalone ? [] : suggestionMarks),
    // A form field has no comments.
    ...(opts.standalone ? [] : [CommentAnchor]),
  ]
}
