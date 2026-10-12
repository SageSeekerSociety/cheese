// A canvas: a free board of cards and lines, kept outside the text.
//
//   :::canvas 6f1c2e0a-3b7d-4e58-9a51-0c2d4b8e7f13 项目地图
//   各件事和它们之间的关系。
//   :::
//
// The opening line names the canvas by its id and gives its title; the line
// under it says what the board shows. The board itself is stored by the
// platform (backend `app/domain/canvas`, which reads and writes these same
// lines in `block.py`). The id is written out, not hidden, so a document
// rewritten as Markdown keeps it, and the text read anywhere else is still a
// titled, explained block. `new` in place of the id is a block whose canvas
// was never made.
//
// Part of the document schema module (see ./index.ts): nothing here touches
// the DOM or imports the app. The editor's view of the board is
// components/panels/doc/blocks/CanvasBlockView.vue.

import { mergeAttributes, Node } from '@tiptap/core'

import { readContainer } from './blocks'

/** What the id is written as before the canvas exists. */
export const UNMADE = 'new'

/**
 * The work this page owes the canvas blocks it inserted or pasted, by the mark
 * it put in their `pending`: make a canvas for the block, or copy the one it
 * names. The mark is in the shared document, so everyone sees it; only the page
 * that made it finds it here, so only that page does the work.
 */
const promised = new Map<string, 'make' | 'copy'>()

export function promiseCanvas(work: 'make' | 'copy'): string {
  const mark = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
  promised.set(mark, work)
  return mark
}

export function promisedWork(mark: string): 'make' | 'copy' | null {
  return promised.get(mark) ?? null
}

export function settlePromise(mark: string): void {
  promised.delete(mark)
}

const ID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

/** `<id> <title>` → the id ('' for `new`) and the title; null when the line names no canvas. */
export function canvasHead(info: string): { canvas: string; title: string } | null {
  const [ref = '', ...rest] = info.trim().split(/[ \t]+/)
  if (ref !== UNMADE && !ID.test(ref)) return null
  return { canvas: ref === UNMADE ? '' : ref, title: rest.join(' ') }
}

/** The block as the document writes it. */
export function canvasMarkdown(canvas: string, title: string, about: string): string {
  const flat = (text: string) => text.split(/\s+/).filter(Boolean).join(' ')
  const head = [`:::canvas ${canvas || UNMADE}`, flat(title)].filter(Boolean).join(' ')
  return [head, flat(about), ':::'].filter(Boolean).join('\n')
}

function text(name: string) {
  return {
    default: '',
    parseHTML: (element: HTMLElement) => element.getAttribute(`data-${name}`) ?? '',
    renderHTML: (attributes: Record<string, unknown>) =>
      attributes[name] ? { [`data-${name}`]: attributes[name] as string } : {},
  }
}

export const Canvas = Node.create({
  name: 'canvas',
  group: 'block',
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      canvas: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-canvas') ?? '',
        renderHTML: (attributes) => ({ 'data-canvas': attributes.canvas as string }),
      },
      title: text('title'),
      about: text('about'),
      // Set by the editor that inserted or pasted the block until the block
      // has a canvas of its own (the view makes or copies one). Never written
      // to Markdown: on a reload the block reads what it names.
      pending: text('pending'),
    }
  },
  parseHTML: () => [{ tag: 'div[data-canvas]' }],
  renderHTML: ({ node, HTMLAttributes }) => [
    'div',
    mergeAttributes(HTMLAttributes, { 'data-block': 'canvas' }),
    ['div', { 'data-field': 'title' }, node.attrs.title as string],
    ['p', { 'data-field': 'about' }, node.attrs.about as string],
  ],
  renderText: ({ node }) => [node.attrs.title, node.attrs.about].filter(Boolean).join('\n'),
  markdownTokenName: 'canvas',
  markdownTokenizer: {
    name: 'canvas',
    level: 'block',
    start: (src) => src.search(/^:{3,}canvas\b/m),
    tokenize(src) {
      const found = readContainer(src)
      if (!found || found.name !== 'canvas') return undefined
      const head = canvasHead(found.info)
      if (!head) return undefined
      const about = found.body.split(/\s+/).filter(Boolean).join(' ')
      return { type: 'canvas', raw: found.raw, ...head, about }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode('canvas', { canvas: token.canvas, title: token.title, about: token.about }),
  renderMarkdown: (node) =>
    canvasMarkdown(
      (node.attrs?.canvas as string) ?? '',
      (node.attrs?.title as string) ?? '',
      (node.attrs?.about as string) ?? ''
    ),
})
