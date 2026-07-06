// Shared living-doc markdown machinery.
//
// ONE extension list powers three things: the DocPanel editor, the lossy-load
// check that guards saves, and the round-trip test corpus. If the editor and
// the check ever used different schemas, the check would be meaningless — so
// they can't: both import from here.
//
// 军规 1 (never silently drop content): the doc's source of truth is a markdown
// file in the project's git repo. Any syntax the visual editor can't represent
// would be destroyed by a load→save cycle, so `compareRoundTrip` detects that
// at LOAD time and DocPanel pauses autosave + shows a banner. The escape hatch
// is source mode, which edits the raw markdown and can never be lossy.

import { Extension, InputRule, mergeAttributes } from '@tiptap/core'
import type { AnyExtension } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { Markdown } from '@tiptap/markdown'
import { TableKit } from '@tiptap/extension-table'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import Image from '@tiptap/extension-image'
import type { ImageOptions } from '@tiptap/extension-image'
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import { common, createLowlight } from 'lowlight'

// One lowlight instance (common ≈ 37 languages), shared by every editor.
export const lowlight = createLowlight(common)

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
      ...this.parent?.(),
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
        const { from, to } = state.selection
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
        node.attrs.language ? { 'data-language': node.attrs.language } : {},
      ),
      [
        'code',
        {
          class: node.attrs.language
            ? this.options.languageClassPrefix + node.attrs.language
            : null,
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
          state.tr.replaceWith(
            range.from,
            range.to,
            state.schema.text(text, [linkMark.create({ href })]),
          )
          // Don't carry the link mark into whatever is typed next.
          state.tr.removeStoredMark(linkMark)
        },
      }),
    ]
  },
})

export interface DocExtensionsOptions {
  /** Display-time image src resolver (defaults to identity). */
  resolveImageSrc?: (src: string) => string
}

/** The full extension list for the living-doc editor (and its tests). */
export function docExtensions(opts: DocExtensionsOptions = {}): AnyExtension[] {
  return [
    StarterKit.configure({
      // Replaced by the lowlight-highlighted code block below.
      codeBlock: false,
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
    Markdown,
    TableKit.configure({ table: { resizable: false } }),
    TaskList,
    TaskItem.configure({ nested: true }),
    DocImage.configure({
      resolveSrc: opts.resolveImageSrc ?? ((s: string) => s),
    }),
    DocCodeBlock.configure({ lowlight }),
    MarkdownLinkInput,
  ]
}

// ---------------------------------------------------------------------------
// Round-trip comparison (lossy-load detection)
// ---------------------------------------------------------------------------
//
// `normalizeMarkdown` defines the TOLERATED differences between the markdown
// on disk and what the editor would write back. Anything beyond these rules
// counts as lossy. The rules, explicitly:
//
//   1. Line endings: CRLF/CR → LF.
//   2. Trailing whitespace on each line is ignored.
//   3. Runs of blank lines collapse to one — the editor models a blank line
//      as block separation, "how many" is not representable.
//   4. Leading/trailing blank lines of the document are ignored.
//   5. Table rows: cell padding spaces collapse (| a  | → | a |) and
//      separator dashes collapse (----- → ---) keeping `:` alignment
//      markers — GFM ignores both, so does every renderer.
//   6. Bullet markers `*` / `+` normalize to `-` (same list, different pen).
//   7. Blockquote markers normalize to `> ` per nesting level.
//   8. "Empty" ATX heading trailing #s are NOT normalized (rare, keep strict).
//
// Everything else — dropped constructs, reordered content, lost alignment,
// lost language tags, escaped-away tokens — fails the comparison.

//   9. Lines consisting solely of blockquote markers (`>`) are dropped — the
//      serializer emits them as block separators inside a quote.
//  10. Basic HTML entities (&lt; &gt; &quot; &#39; &amp;) decode to their
//      characters outside code fences — the serializer entity-encodes bare
//      `<` / `&` in prose; same characters, different encoding. (Accepted
//      blind spot: entities inside INLINE code spans also get decoded for
//      comparison; a construct that rare fails safe elsewhere.)
//
// Note on our structured tokens: the serializer HTML-escapes `<`/`&` in text,
// which would corrupt `<@handle>` / `<#topicId>` / `<&path>` on save. That's
// why DocPanel must serialize through `serializeDoc` below, which reverses the
// escaping for EXACTLY those three token shapes (deterministic token syntax —
// never general unescaping, which could turn user-typed literal HTML live).

const ESCAPED_TOKEN_RE =
  /&lt;(@[\w-]+|#[0-9a-fA-F-]{8,}|&amp;[\w./一-鿿-]+)&gt;/g

// A pure autolink serializes as `[url](url)`; write the bare URL back so the
// file stays byte-stable (GFM re-autolinks it on the next parse). The mark's
// renderMarkdown can't do this — the manager splits marks into open/close
// around a placeholder, so text===href is only visible after serialization.
const AUTOLINK_RT_RE = /\[(https?:\/\/[^\s\]]+|mailto:[^\s\]]+)\]\(\1\)/g

// Apply `fn` to prose only: skip fenced code lines entirely and inline code
// spans within a line, so literal `[x](x)` / `&lt;` inside code is never touched.
function mapProse(md: string, fn: (seg: string) => string): string {
  let inFence = false
  return md
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence
        return line
      }
      if (inFence) return line
      // Chunk the line into code spans (`…`) and prose; transform prose only.
      return line
        .split(/(`+[^`]*`+)/)
        .map((chunk, i) => (i % 2 === 1 ? chunk : fn(chunk)))
        .join('')
    })
    .join('\n')
}

/** Serialize the editor to markdown, restoring our structured tokens. */
export function serializeDoc(editor: {
  getMarkdown: () => string
}): string {
  return mapProse(editor.getMarkdown(), (seg) =>
    seg
      .replace(
        ESCAPED_TOKEN_RE,
        (_m, inner: string) => `<${inner.replace(/^&amp;/, '&')}>`,
      )
      .replace(AUTOLINK_RT_RE, '$1'),
  )
}

const TABLE_ROW_RE = /^\s*\|.*\|\s*$/
const TABLE_SEP_CELL_RE = /^\s*(:?)-+(:?)\s*$/

// Rule 10: decode the five basic entities. `&lt;` first, `&amp;` LAST so a
// literal `&amp;lt;` decodes to `&lt;` (one level), never all the way to `<`.
function decodeBasicEntities(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
}

function normalizeTableRow(line: string): string {
  // Split naïvely on '|': escaped pipes are rare and both sides of the
  // comparison get the same treatment, so equality is still meaningful.
  const inner = line.trim().slice(1, -1)
  const cells = inner.split('|').map((c) => {
    const sep = c.match(TABLE_SEP_CELL_RE)
    if (sep) return `${sep[1]}---${sep[2]}`
    return c.trim().replace(/\s+/g, ' ')
  })
  return `| ${cells.join(' | ')} |`
}

export function normalizeMarkdown(md: string): string {
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
  // Each entry keeps whether the line is fence content, so the blank-line
  // collapse below never touches the inside of a code block.
  const out: { text: string; literal: boolean }[] = []
  let inFence = false
  for (let raw of lines) {
    const fence = raw.match(/^(\s*)(```|~~~)/)
    if (fence) inFence = !inFence
    if (inFence || fence) {
      // Inside code fences everything is literal — only strip trailing WS on
      // the fence lines themselves, keep code lines byte-exact.
      out.push({ text: fence ? raw.trimEnd() : raw, literal: !fence })
      continue
    }
    raw = raw.replace(/\s+$/, '')
    if (TABLE_ROW_RE.test(raw)) {
      out.push({ text: decodeBasicEntities(normalizeTableRow(raw)), literal: false })
      continue
    }
    // A line of only quote markers is the serializer's block separator inside
    // a quote — structural noise for comparison purposes: drop it.
    if (/^(\s{0,3}>\s*)+$/.test(raw)) {
      continue
    }
    // Blockquote markers: ">text", "> text", ">  > text" → "> " per level.
    const bq = raw.match(/^((?:\s{0,3}>\s?)+)(.*)$/)
    if (bq) {
      const depth = (bq[1].match(/>/g) ?? []).length
      raw = '> '.repeat(depth) + bq[2].trim()
      out.push({ text: decodeBasicEntities(raw.trimEnd()), literal: false })
      continue
    }
    // Bullet markers * / + → - (preserve indentation).
    raw = raw.replace(/^(\s*)[*+](\s)/, '$1-$2')
    out.push({ text: decodeBasicEntities(raw), literal: false })
  }
  // Collapse blank-line runs (never inside fences); trim document edges.
  const collapsed: string[] = []
  let prevBlank = false
  for (const l of out) {
    if (!l.literal && l.text === '') {
      if (prevBlank) continue
      prevBlank = true
    } else {
      prevBlank = false
    }
    collapsed.push(l.text)
  }
  while (collapsed[0] === '') collapsed.shift()
  while (collapsed[collapsed.length - 1] === '') collapsed.pop()
  return collapsed.join('\n')
}

export interface RoundTripReport {
  clean: boolean
  /** First few differing normalized lines, for console.debug forensics. */
  diff: string
}

/** Compare original markdown vs its parse→serialize round trip. */
export function compareRoundTrip(original: string, roundTripped: string): RoundTripReport {
  const a = normalizeMarkdown(original)
  const b = normalizeMarkdown(roundTripped)
  if (a === b) return { clean: true, diff: '' }
  const al = a.split('\n')
  const bl = b.split('\n')
  const diffs: string[] = []
  const n = Math.max(al.length, bl.length)
  for (let i = 0; i < n && diffs.length < 12; i++) {
    if (al[i] !== bl[i]) {
      diffs.push(`  L${i + 1}`)
      diffs.push(`  - ${al[i] ?? '∅'}`)
      diffs.push(`  + ${bl[i] ?? '∅'}`)
    }
  }
  return { clean: false, diff: diffs.join('\n') }
}
