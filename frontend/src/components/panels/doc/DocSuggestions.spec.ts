// @vitest-environment jsdom
// A suggestion the AI teammate left in the shared document is decided by a
// person in the panel, and the decision is the text every other reader gets:
// accepting makes the proposed text final, rejecting leaves the text as it was.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { transformToSuggestionTransaction } from '@handlewithcare/prosemirror-suggest-changes'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { localDocSession } from '../../../lib/docLocalSession'
import { docExtensions, exportMarkdown, liveSuggestions, suggestionId, writeMarkdown } from '../../../lib/docSchema'
import { DOC_TOPIC, docPanelProps } from '../../../views/demo/catalogFixtures'
import PanelDocView from '../PanelDocView.vue'

import { setLocale, t } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

const editors: Editor[] = []
beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
})
afterEach(() => {
  cleanup()
  editors.splice(0).forEach((editor) => editor.destroy())
  vi.unstubAllGlobals()
})

const TEXT = '数据量到一千万行时开始评估迁移。\n\n双写一周，对账无误后切过去。'

/** The panel on one copy of the document, and another reader on another copy. */
function twoReaders(suggestionReasons: Record<string, string> = {}) {
  const mine = new Y.Doc()
  const theirs = new Y.Doc()
  writeMarkdown(theirs, TEXT)
  Y.applyUpdate(mine, Y.encodeStateAsUpdate(theirs))
  mine.on('update', (u: Uint8Array, origin: unknown) => origin !== 'peer' && Y.applyUpdate(theirs, u, 'peer'))
  theirs.on('update', (u: Uint8Array, origin: unknown) => origin !== 'peer' && Y.applyUpdate(mine, u, 'peer'))
  const other = new Editor({ extensions: [...docExtensions(), Collaboration.configure({ document: theirs })] })
  editors.push(other)
  render(PanelDocView, {
    props: docPanelProps({
      topic: { ...DOC_TOPIC },
      session: localDocSession('', DOC_TOPIC.id, mine),
      suggestionReasons,
    }),
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { theirs, other }
}

/** The agent, on the other copy, proposes replacing `from` with `to`. */
function suggest(editor: Editor, from: string, to: string, id = suggestionId('cheese')) {
  let at = -1
  editor.state.doc.descendants((node, pos) => {
    if (at < 0 && node.isText && node.text?.includes(from)) at = pos + node.text.indexOf(from)
  })
  const tr = editor.state.tr.insertText(to, at, at + from.length)
  editor.view.dispatch(transformToSuggestionTransaction(tr, editor.state, () => id))
}

describe('deciding the AI teammate’s suggestions', () => {
  it('accepting one makes its text final for the other reader', async () => {
    const { theirs, other } = twoReaders()
    suggest(other, '一千万', '五百万')
    suggest(other, '一周', '两周')

    await screen.findByText(t('work.room.docSuggest.pending', { agent: '芝士', n: 2 }))
    await fireEvent.click(screen.getByRole('button', { name: t('work.room.docEdit.next') }))
    await fireEvent.click(await screen.findByRole('button', { name: t('work.room.docSuggest.accept') }))

    await waitFor(() => expect(exportMarkdown(theirs)).toContain('数据量到五百万行'))
    expect(exportMarkdown(theirs)).toContain('双写一周')
    expect(liveSuggestions(theirs)).toEqual([expect.objectContaining({ old: '一周', new: '两周' })])
  })

  it('rejecting all of them leaves the text as it was', async () => {
    const { theirs, other } = twoReaders()
    suggest(other, '一千万', '五百万')
    suggest(other, '一周', '两周')

    await fireEvent.click(await screen.findByRole('button', { name: t('work.room.docSuggest.rejectAll') }))

    await waitFor(() => expect(liveSuggestions(theirs)).toEqual([]))
    expect(exportMarkdown(theirs)).toBe(TEXT)
    expect(other.getText()).not.toContain('五百万')
  })

  it('says why it was suggested', async () => {
    const { other } = twoReaders({ 'cheese:limit': '测试里五百万行时已经到 120 ms' })
    suggest(other, '一千万', '五百万', 'cheese:limit')

    await fireEvent.click(await screen.findByRole('button', { name: t('work.room.docEdit.next') }))

    expect(await screen.findByText('测试里五百万行时已经到 120 ms')).toBeTruthy()
  })
})
