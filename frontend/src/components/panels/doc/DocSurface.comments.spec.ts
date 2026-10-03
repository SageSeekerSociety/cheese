// @vitest-environment jsdom
import type { Editor } from '@tiptap/core'
import type { Block } from '../../../cx_types'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { usePanelDoc } from '../../../composables/usePanelDoc'
import { commentMarkKey } from '../../../lib/docDecorations'
import { localDocSession } from '../../../lib/docLocalSession'

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
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
async function mountDoc(html: string, fetch?: () => Promise<Block[]>, askAgent = true) {
  const topicId = ref(`surface-comment-${++serial}`)
  const editable = ref(true)
  const openId = ref<string | null>(null)
  const surface = ref<InstanceType<typeof DocSurface> | null>(null)
  const nodes = [{ id: 'server-node-0', content: 'document' }] as Block[]
  let data!: ReturnType<typeof usePanelDoc>
  const captured: { anchorId: string | null; quote: string }[] = []
  const asked: { anchorId: string | null; quote: string; from: number; to: number }[] = []
  const located: string[] = []
  const fetchNodes = vi.fn(fetch ?? (async () => nodes))
  const session = localDocSession()
  const view = render(
    defineComponent({
      setup() {
        data = usePanelDoc({ topic: null, activityTick: 0, topicList: [] })
        data.anchorNodes.value = nodes
        return () =>
          h('div', { class: 'doc-reading' }, [
            h('div', { class: 'doc-body' }, [
              h(DocSurface, {
                ref: surface,
                topicId: topicId.value,
                editable: editable.value,
                loading: false,
                session,
                imageSrc: (src: string) => src,
                pulse: () => {},
                fetchDocNodes: fetchNodes,
                commentMarkIndex: data.commentMarkIndex.value,
                openCommentId: openId.value,
                agentName: '芝士',
                agentHandle: askAgent ? 'cheese' : null,
                onOpenComment: (payload: { anchorId: string | null; quote: string }) => captured.push(payload),
                onAgent: (payload: { anchorId: string | null; quote: string; from: number; to: number }) =>
                  asked.push(payload),
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
  return { ...view, ed, surface, topicId, editable, openId, data, nodes, captured, asked, located, fetchNodes }
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
function marks(ed: Editor) {
  return commentMarkKey
    .getState(ed.state)
    .find()
    .map((d: { from: number; to: number }) => ({
      from: d.from,
      to: d.to,
      text: ed.state.doc.textBetween(d.from, d.to, ' '),
    }))
}

describe('production surface comment selections', () => {
  it.each([
    '<ul><li><p>父</p><ul><li><p>😀目标</p></li></ul></li></ul>',
    '<p>前 <strong>粗体</strong> <a href="https://example.test">😀目标</a> 尾</p>',
  ])('restores the actual descendant PM span: %s', async (html) => {
    const f = await mountDoc(html)
    const selected = await select(f.ed, '😀目标')
    const button = commentAction()
    const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
    button.dispatchEvent(down)
    expect(down.defaultPrevented).toBe(true)
    expect(f.ed.state.selection.from).toBe(selected.from)
    expect(f.ed.state.selection.to).toBe(selected.to)
    await fireEvent.click(button)
    await waitFor(() => expect(f.captured).toEqual([{ anchorId: 'server-node-0', quote: '😀目标' }]))
    f.data.comments.value = [{ id: 'c1', reply_to: 'server-node-0', anchor_quote: '😀目标' }] as Block[]
    await nextTick()
    expect(marks(f.ed)).toEqual([{ ...selected, text: '😀目标' }])
    expect(f.surface.value!.commentQuoteState('c1')).toBe('unique')
    f.openId.value = 'c1'
    await nextTick()
    expect(document.querySelector('[data-comment="c1"]')?.classList.contains('is-active')).toBe(true)
    await fireEvent.click(document.querySelector('[data-comment="c1"]')!)
    expect(f.located).toEqual(['c1'])
    // Changing only active card must not re-zip stale server indices after an edit.
    f.ed.view.dispatch(f.ed.state.tr.insertText('新增 ', selected.from))
    const mapped = marks(f.ed)
    f.openId.value = null
    await nextTick()
    expect(marks(f.ed)).toEqual(mapped)
  })

  it('discloses repeated quote ambiguity instead of underlining the first occurrence', async () => {
    const f = await mountDoc('<p>前 目标 中 目标 尾</p>')
    const second = await select(f.ed, '目标', 1)
    expect(second.from).toBeGreaterThan(span(f.ed, '目标', 0).from)
    await fireEvent.click(commentAction())
    await waitFor(() => expect(f.captured).toEqual([{ anchorId: 'server-node-0', quote: '目标' }]))
    f.data.comments.value = [{ id: 'repeat', reply_to: 'server-node-0', anchor_quote: '目标' }] as Block[]
    await nextTick()
    expect(marks(f.ed)).toEqual([])
    expect(f.surface.value!.commentQuoteState('repeat')).toBe('ambiguous')
  })

  it('keeps the page-comment fallback when server nodes do not align', async () => {
    const f = await mountDoc('<p>前 目标 后</p>', async () => [{ id: 'a' }, { id: 'b' }] as Block[])
    await select(f.ed, '目标')
    await fireEvent.click(commentAction())
    await waitFor(() => expect(f.captured).toEqual([{ anchorId: null, quote: '目标' }]))
    expect(marks(f.ed)).toEqual([])
  })

  it('uses the pre-click quote even if selection changes during the API wait', async () => {
    const pending = deferred<Block[]>()
    const f = await mountDoc('<p>第一段 第二段</p>', () => pending.promise)
    await select(f.ed, '第一段')
    await fireEvent.click(commentAction())
    f.ed.commands.setTextSelection(span(f.ed, '第二段'))
    pending.resolve(f.nodes)
    await waitFor(() => expect(f.captured).toEqual([{ anchorId: 'server-node-0', quote: '第一段' }]))
  })

  it.each(['topic', 'document', 'unmount'] as const)('ignores a late node receipt after %s changes', async (change) => {
    const pending = deferred<Block[]>()
    const f = await mountDoc('<p>原文</p>', () => pending.promise)
    await select(f.ed, '原文')
    await fireEvent.click(commentAction())
    if (change === 'topic') f.topicId.value += '-next'
    else if (change === 'document') f.ed.view.dispatch(f.ed.state.tr.insertText('变更', 1))
    else f.unmount()
    await nextTick()
    pending.resolve(f.nodes)
    await Promise.resolve()
    await nextTick()
    expect(f.captured).toEqual([])
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

  it('keeps mapped identity across a new top-level block and hides a newly ambiguous quote', async () => {
    const f = await mountDoc('<p>原文</p>')
    f.data.comments.value = [{ id: 'mapped', reply_to: 'server-node-0', anchor_quote: '原文' }] as Block[]
    await nextTick()
    expect(f.surface.value!.commentQuoteState('mapped')).toBe('unique')
    const paragraph = f.ed.schema.nodes.paragraph.create(null, f.ed.schema.text('插入段'))
    f.ed.view.dispatch(f.ed.state.tr.insert(0, paragraph))
    f.openId.value = 'mapped'
    await nextTick()
    expect(f.surface.value!.commentQuoteState('mapped')).toBe('unique')
    expect(document.querySelector('[data-comment="mapped"]')?.textContent).toBe('原文')
    const original = span(f.ed, '原文')
    f.ed.view.dispatch(f.ed.state.tr.insertText(' 原文', original.to))
    await nextTick()
    expect(f.surface.value!.commentQuoteState('mapped')).toBe('ambiguous')
    expect(document.querySelector('[data-comment="mapped"]')).toBeNull()
  })

  it('does not move a changed mapped quote onto another matching occurrence', async () => {
    const f = await mountDoc('<p>原文</p>')
    f.data.comments.value = [{ id: 'changed', reply_to: 'server-node-0', anchor_quote: '原文' }] as Block[]
    await nextTick()
    const original = span(f.ed, '原文')
    f.ed.view.dispatch(f.ed.state.tr.insertText('原文 新', original.from, original.to))
    await nextTick()
    expect(f.surface.value!.commentQuoteState('changed')).toBe('missing')
    expect(document.querySelector('[data-comment="changed"]')).toBeNull()
  })
})

describe('asking the AI teammate from a selection', () => {
  it('hands it the selected text, where it is, and what it is anchored to', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>')
    const selected = await select(f.ed, '一千万')
    await fireEvent.click(screen.getByRole('button', { name: '芝士' }))
    await waitFor(() => expect(f.asked).toEqual([{ anchorId: 'server-node-0', quote: '一千万', ...selected }]))
  })

  it('is offered on a read-only document too', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>')
    f.editable.value = false
    await nextTick()
    await select(f.ed, '一千万')
    expect(screen.getByRole('button', { name: '芝士' })).toBeTruthy()
  })

  it('is not offered when the teammate cannot be named', async () => {
    const f = await mountDoc('<p>数据量到一千万行时开始评估。</p>', undefined, false)
    await select(f.ed, '一千万')
    expect(screen.queryByRole('button', { name: '芝士' })).toBeNull()
  })
})
