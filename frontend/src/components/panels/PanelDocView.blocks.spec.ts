// @vitest-environment jsdom
// The controls a block carries (a callout's kind, a timeline item's menu, a
// stat card's ＋ and ×, a column's menu) work while the document can be edited
// and stand down while it is read. The stylesheet disables them on any page
// marked as a reading surface, so the editable panel must not carry that mark
// anywhere around its text.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { localDocSession } from '../../lib/docLocalSession'
import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import { READING } from './doc/blocks/shapes'
import PanelDocView from './PanelDocView.vue'

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

const TEXT = '> [!TIP]\n> 先灰度一周。\n\n:::stats\n- 日均搜索 | 2,140 次 | +104%\n:::'

function open(editable: boolean) {
  render(PanelDocView, {
    props: docPanelProps({
      topic: { ...DOC_TOPIC },
      session: localDocSession(TEXT, DOC_TOPIC.id),
      editable,
    }),
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('block controls in the document panel', () => {
  it('are live while the document can be edited', async () => {
    open(true)
    const kind = await screen.findByRole('button', { name: t('work.room.doc.blocks.calloutKind') })
    expect(kind.closest(`.${READING}`)).toBeNull()
  })

  it('stand down while the document is read', async () => {
    open(false)
    const kind = await screen.findByRole('button', { name: t('work.room.doc.blocks.calloutKind') })
    expect(kind.closest(`.${READING}`)).not.toBeNull()
  })
})
