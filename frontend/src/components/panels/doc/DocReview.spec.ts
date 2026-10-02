// @vitest-environment jsdom
// 「查看改动」 lists the changes someone asked the AI teammate for, and restoring
// one puts its original text back in that person's name; a change whose new text
// is gone from the document counts as restored.
import type { DocEdit } from '../../../lib/docEdits'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Y from 'yjs'

import { localDocSession } from '../../../lib/docLocalSession'
import { exportMarkdown, writeMarkdown } from '../../../lib/docSchema'
import { DOC_TOPIC, docPanelProps } from '../../../views/demo/catalogFixtures'
import PanelDocView from '../PanelDocView.vue'

import { setLocale, t } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

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
  vi.unstubAllGlobals()
})

const EDITS: DocEdit[] = [
  { old: '数据量到一千万行时开始评估迁移。', new: '数据量到五百万行时开始评估迁移。' },
  { old: '双写一周，对账无误后切过去。', new: '双写一周，期间以 PostgreSQL 为准，对账无误后切过去。' },
]

function open() {
  const doc = new Y.Doc()
  writeMarkdown(doc, `${EDITS[0].new}\n\n${EDITS[1].new}`)
  // The service: an edit replaces its old text, once.
  const applyDocEdits = vi.fn(async (edits: DocEdit[]) => {
    for (const edit of edits) writeMarkdown(doc, exportMarkdown(doc).replace(edit.old, edit.new))
  })
  const panel = ref<InstanceType<typeof PanelDocView> | null>(null)
  const props = docPanelProps({ topic: { ...DOC_TOPIC }, session: localDocSession('', DOC_TOPIC.id, doc) })
  render(defineComponent({ setup: () => () => h(PanelDocView, { ...props, applyDocEdits, ref: panel }) }), {
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { doc, applyDocEdits, panel }
}

describe('reviewing the changes someone asked for', () => {
  it('restores one change in the reader’s name and counts it as restored', async () => {
    const { doc, applyDocEdits, panel } = open()
    await waitFor(() => expect(panel.value).toBeTruthy())
    await waitFor(() => expect(document.querySelector('.doc-prose')?.textContent).toContain('五百万'))

    panel.value!.reviewEdits({ requester: '李老师', edits: EDITS })
    await screen.findByText(t('work.room.docReview.title', { requester: '李老师', agent: '芝士', n: 2 }))

    await fireEvent.click(await screen.findByRole('button', { name: t('work.room.docReview.restore') }))
    expect(applyDocEdits).toHaveBeenCalledWith([{ old: EDITS[0].new, new: EDITS[0].old }])
    expect(exportMarkdown(doc)).toContain('一千万')

    await screen.findByText(t('work.room.docReview.title', { requester: '李老师', agent: '芝士', n: 1 }))
  })

  it('counts a change whose new text was edited away as restored', async () => {
    const { doc, panel } = open()
    await waitFor(() => expect(document.querySelector('.doc-prose')?.textContent).toContain('五百万'))
    writeMarkdown(doc, `${EDITS[0].old}\n\n${EDITS[1].old}`)

    panel.value!.reviewEdits({ requester: '李老师', edits: EDITS })

    await screen.findByText(t('work.room.docReview.allRestored', { requester: '李老师', agent: '芝士' }))
    expect(screen.queryByRole('button', { name: t('work.room.docReview.restore') })).toBeNull()
  })
})
