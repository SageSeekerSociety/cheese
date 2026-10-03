// @vitest-environment jsdom
import type { Editor } from '@tiptap/core'
import type { Block } from '../../../cx_types'
import type { CommentSpot } from '../../../lib/docCommentSpots'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { anchorComment, placeOf } from '../../../lib/docCommentSpots'
import { localDocSession } from '../../../lib/docLocalSession'
import { commentAnchors } from '../../../lib/docSchema'

import DocSurface from './DocSurface.vue'

import { setLocale, t } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

let frames: Map<number, FrameRequestCallback>
let frameId: number
let bodyRight: number
let sidebarLeft: number | null
let selectionLeft: number
let scroll: number
let serial = 0
const editors: Editor[] = []
function rect(left: number, top: number, right: number, bottom: number): DOMRect {
  return {
    left,
    top,
    right,
    bottom,
    width: right - left,
    height: bottom - top,
    x: left,
    y: top,
    toJSON: () => ({}),
  } as DOMRect
}
function flushFrames() {
  const pending = [...frames.values()]
  frames.clear()
  pending.forEach((fn) => fn(0))
}
beforeEach(() => {
  setLocale('zh-CN')
  frames = new Map()
  frameId = 0
  bodyRight = 700
  sidebarLeft = null
  selectionLeft = 300
  scroll = 0
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => {
    frames.set(++frameId, fn)
    return frameId
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.matches('[data-comments-panel]')) return rect(sidebarLeft ?? bodyRight, 40, bodyRight, 600)
    if (this.classList.contains('doc-body') || this.classList.contains('doc-reading'))
      return rect(100, 40, bodyRight, 600)
    if (this.classList.contains('doc-editor-wrap')) return rect(100, 60 - scroll, bodyRight, 800 - scroll)
    return rect(100, 60 - scroll, bodyRight, 600)
  })
})
afterEach(() => {
  cleanup()
  for (const ed of editors.splice(0)) if (!ed.isDestroyed) ed.destroy()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
async function mountDoc(html: string, askAgent = true) {
  const topicId = ref(`surface-comment-${++serial}`)
  const editable = ref(true)
  const canComment = ref(true)
  const openThreads = ref<ReadonlySet<string>>(new Set())
  const activeThread = ref<string | null>(null)
  const surface = ref<InstanceType<typeof DocSurface> | null>(null)
  const captured: CommentSpot[] = []
  const asked: CommentSpot[] = []
  const located: string[] = []
  const session = localDocSession()
  const view = render(
    defineComponent({
      setup() {
        return () =>
          h('div', { class: 'doc-reading' }, [
            h('div', { class: 'doc-body' }, [
              h(DocSurface, {
                ref: surface,
                topicId: topicId.value,
                editable: editable.value,
                canComment: canComment.value,
                loading: false,
                session,
                imageSrc: (src: string) => src,
                pulse: () => {},
                fetchDocNodes: async () => [] as Block[],
                openThreads: openThreads.value,
                activeThread: activeThread.value,
                agentName: '芝士',
                agentHandle: askAgent ? 'cheese' : null,
                onOpenComment: (payload: CommentSpot) => captured.push(payload),
                onAgent: (payload: CommentSpot) => asked.push(payload),
                onLocateComment: (id: string) => located.push(id),
              }),
            ]),
            h('aside', { 'data-comments-panel': '', style: { display: sidebarLeft === null ? 'none' : 'block' } }),
          ])
      },
    }),
    { global: { plugins: [createVuetify()], stubs: { VIcon: { template: '<span><slot /></span>' } } } }
  )
  await waitFor(() => expect(surface.value?.editor).toBeTruthy())
  const ed = surface.value!.editor as Editor
  editors.push(ed)
  ed.commands.setContent(html)
  vi.spyOn(ed.view, 'coordsAtPos').mockImplementation(() => ({
    top: 200 - scroll,
    bottom: 224 - scroll,
    left: selectionLeft,
    right: selectionLeft + 30,
  }))
  await nextTick()
  return {
    ...view,
    ed,
    surface,
    topicId,
    editable,
    canComment,
    openThreads,
    activeThread,
    captured,
    asked,
    located,
  }
}
function span(ed: Editor, quote: string, occurrence = 0) {
  const spans: { from: number; to: number }[] = []
  ed.state.doc.descendants((node, pos) => {
    if (!node.isText) return
    const text = node.text ?? ''
    for (let at = text.indexOf(quote); at >= 0; at = text.indexOf(quote, at + 1)) {
      spans.push({ from: pos + at, to: pos + at + quote.length })
    }
  })
  expect(spans.length).toBeGreaterThan(occurrence)
  return spans[occurrence]
}
async function select(ed: Editor, quote: string, occurrence = 0) {
  const selected = span(ed, quote, occurrence)
  ed.commands.setTextSelection(selected)
  await waitFor(() => expect(document.querySelector('.doc-comment-cta')).not.toBeNull())
  await nextTick()
  flushFrames()
  await nextTick()
  return selected
}
function commentAction() {
  return screen.getByRole('button', { name: t('work.room.doc.commentOnSelection') })
}
/** What is highlighted in the text, by thread. */
function highlighted(): Record<string, string> {
  const out: Record<string, string> = {}
  document.querySelectorAll<HTMLElement>('[data-comment]').forEach((el) => {
    out[el.dataset.comment!] = (out[el.dataset.comment!] ?? '') + el.textContent
  })
  return out
}
async function commentOn(f: Awaited<ReturnType<typeof mountDoc>>, quote: string, thread: string, occurrence = 0) {
  await select(f.ed, quote, occurrence)
  await fireEvent.click(commentAction())
  const spot = f.captured[f.captured.length - 1]
  expect(anchorComment(f.ed, spot, thread)).toBe(true)
  f.openThreads.value = new Set([...f.openThreads.value, thread])
  await nextTick()
  return spot
}

describe('production surface comment selections', () => {
  it.each([
    '<ul><li><p>父</p><ul><li><p>😀目标</p></li></ul></li></ul>',
    '<p>前 <strong>粗体</strong> <a href="https://example.test">😀目标</a> 尾</p>',
  ])('marks exactly the selected words, and points back to the thread: %s', async (html) => {
    const f = await mountDoc(html)
    const selected = await select(f.ed, '😀目标')
    const button = commentAction()
    const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
    button.dispatchEvent(down)
    expect(down.defaultPrevented).toBe(true)
    expect(f.ed.state.selection.from).toBe(selected.from)
    expect(f.ed.state.selection.to).toBe(selected.to)
    await fireEvent.click(button)
    expect(f.captured).toEqual([expect.objectContaining({ quote: '😀目标', ...selected })])
    expect(anchorComment(f.ed, f.captured[0], 'c1')).toBe(true)
    f.openThreads.value = new Set(['c1'])
    await nextTick()
    expect(highlighted()).toEqual({ c1: '😀目标' })
    f.activeThread.value = 'c1'
    await nextTick()
    expect(document.querySelector('[data-comment="c1"]')?.classList.contains('is-active')).toBe(true)
    await fireEvent.click(document.querySelector('[data-comment="c1"]')!)
    expect(f.located).toEqual(['c1'])
  })

  it('marks the occurrence that was selected when the same words appear twice', async () => {
    const f = await mountDoc('<p>前 目标 中 目标 尾</p>')
    const second = span(f.ed, '目标', 1)
    await commentOn(f, '目标', 'second', 1)
    expect(highlighted()).toEqual({ second: '目标' })
    expect(commentAnchors(f.ed.state.doc).get('second')).toEqual([second])
  })

  it('keeps the words marked when a paragraph is inserted above and text typed before them', async () => {
    const f = await mountDoc('<p>数据量到五百万行就评估。</p>')
    await commentOn(f, '五百万行', 't1')
    const paragraph = f.ed.schema.nodes.paragraph.create(null, f.ed.schema.text('插入段'))
    f.ed.view.dispatch(f.ed.state.tr.insert(0, paragraph))
    f.ed.view.dispatch(f.ed.state.tr.insertText('先用 PostgreSQL，', span(f.ed, '数据量').from))
    await nextTick()
    expect(highlighted()).toEqual({ t1: '五百万行' })
    expect(f.surface.value!.threadPlace('t1')).toBe('marked')
  })

  it('stops highlighting a resolved thread, and keeps where deleted words were', async () => {
    const f = await mountDoc('<p>第一段。</p><p>数据量到五百万行就评估。</p>')
    await commentOn(f, '五百万行', 't1')
    f.openThreads.value = new Set()
    await nextTick()
    expect(highlighted()).toEqual({})
    expect(f.surface.value!.threadPlace('t1')).toBe('marked')
    const words = span(f.ed, '五百万行')
    f.ed.view.dispatch(f.ed.state.tr.delete(words.from, words.to))
    await nextTick()
    expect(f.surface.value!.threadPlace('t1')).toBe('placed')
    // Where it was is still in the second paragraph.
    expect(f.ed.state.doc.resolve(placeOf(f.ed, 't1')!).parent.textContent).toBe('数据量到就评估。')
  })

  it('does not move onto words typed over its own', async () => {
    const f = await mountDoc('<p>数据量到五百万行就评估。</p>')
    await commentOn(f, '五百万行', 't1')
    const words = span(f.ed, '五百万行')
    f.ed.view.dispatch(f.ed.state.tr.insertText('一千万条', words.from, words.to))
    await nextTick()
    expect(highlighted()).toEqual({})
    expect(f.surface.value!.threadPlace('t1')).toBe('placed')
  })

  it('offers no comment on a document that takes none', async () => {
    const f = await mountDoc('<p>原文</p>')
    f.canComment.value = false
    await nextTick()
    await select(f.ed, '原文')
    expect(screen.queryByRole('button', { name: t('work.room.doc.commentOnSelection') })).toBeNull()
  })

  it('Escape dismisses an unchanged selection until a different selection is made', async () => {
    const f = await mountDoc('<p>第一段 第二段</p>')
    await select(f.ed, '第一段')
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    f.ed.view.dom.dispatchEvent(event)
    await nextTick()
    expect(event.defaultPrevented).toBe(true)
    expect(document.querySelector('.doc-comment-cta')).toBeNull()
    f.ed.emit('selectionUpdate', { editor: f.ed, transaction: f.ed.state.tr })
    window.dispatchEvent(new Event('resize'))
    flushFrames()
    await nextTick()
    expect(document.querySelector('.doc-comment-cta')).toBeNull()
    await select(f.ed, '第二段')
    expect(document.querySelector('.doc-comment-cta')).not.toBeNull()
  })

  it('clamps against a 390px pane inside a wide window and repositions on scroll', async () => {
    bodyRight = 490
    selectionLeft = 480
    const f = await mountDoc('<p>原文</p>')
    await select(f.ed, '原文')
    let button = document.querySelector<HTMLElement>('.doc-comment-cta')!
    const left = Number.parseFloat(button.style.left) + 100
    expect(left).toBeGreaterThanOrEqual(104)
    expect(left + 96).toBeLessThanOrEqual(486)
    const top = Number.parseFloat(button.style.top)
    scroll = 80
    document.dispatchEvent(new Event('scroll'))
    flushFrames()
    await nextTick()
    button = document.querySelector<HTMLElement>('.doc-comment-cta')!
    expect(Number.parseFloat(button.style.top)).toBe(top)
    expect(Number.parseFloat(button.style.top) + 60 - scroll).toBeGreaterThanOrEqual(44)
  })

  it('excludes the visible comment drawer from selection-toolbar space', async () => {
    bodyRight = 700
    sidebarLeft = 430
    selectionLeft = 600
    const f = await mountDoc('<p>原文</p>')
    await select(f.ed, '原文')
    const button = document.querySelector<HTMLElement>('.doc-comment-cta')!
    expect(Number.parseFloat(button.style.left) + 100 + 96).toBeLessThanOrEqual(426)
  })

  it('never offers an empty selection, offers no formatting on a read-only one, and removes global listeners', async () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    const f = await mountDoc('<p>原文</p>')
    f.ed.commands.setTextSelection(1)
    await nextTick()
    expect(document.querySelector('.doc-comment-cta')).toBeNull()
    f.editable.value = false
    await nextTick()
    await select(f.ed, '原文')
    expect(commentAction()).toBeTruthy()
    expect(screen.queryByRole('button', { name: t('work.room.doc.format.bold') })).toBeNull()
    expect(screen.getByRole('button', { name: t('work.room.doc.copy') })).toBeTruthy()
    f.unmount()
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function), true)
    expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function), true)
  })

  it('formats exactly the selection and stays over it', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>')
    await select(f.ed, '一千万')
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.format.bold') }))
    expect(f.ed.getHTML()).toContain('数据量到<strong>一千万</strong>行')
    await nextTick()
    expect(document.querySelector('.doc-comment-cta')).not.toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.format.highlight') }))
    expect(f.ed.getHTML()).toContain('<mark>一千万</mark>')
  })

  it('changes the paragraph style of every paragraph the selection touches, and no other', async () => {
    const f = await mountDoc('<p>第一段文字</p><p>第二段文字</p><p>第三段文字</p>')
    await select(f.ed, '一段')
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.blockType') }))
    await fireEvent.click(screen.getByRole('menuitemradio', { name: new RegExp(t('work.room.doc.slash.h2')) }))
    expect(f.ed.getHTML()).toBe('<h2>第一段文字</h2><p>第二段文字</p><p>第三段文字</p>')

    f.ed.commands.setTextSelection({ from: span(f.ed, '一段').from, to: span(f.ed, '第二').to })
    await nextTick()
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.blockType') }))
    await fireEvent.click(screen.getByRole('menuitemradio', { name: new RegExp(t('work.room.doc.slash.h3')) }))
    expect(f.ed.getHTML()).toBe('<h3>第一段文字</h3><h3>第二段文字</h3><p>第三段文字</p>')
  })
})

describe('asking the AI teammate from a selection', () => {
  it('hands it the selected text and where it is', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>')
    const selected = await select(f.ed, '一千万')
    await fireEvent.click(screen.getByRole('button', { name: '芝士' }))
    expect(f.asked).toEqual([expect.objectContaining({ quote: '一千万', ...selected })])
  })

  it('is offered on a read-only document too', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>')
    f.editable.value = false
    await nextTick()
    await select(f.ed, '一千万')
    expect(screen.getByRole('button', { name: '芝士' })).toBeTruthy()
  })

  it('is not offered when the teammate cannot be named', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>', false)
    await select(f.ed, '一千万')
    expect(screen.queryByRole('button', { name: '芝士' })).toBeNull()
  })
})
