// @vitest-environment jsdom
// The controls a block carries (a callout's kind, a timeline item's menu, a
// stat card's ＋ and ×, a column's menu) work while the document can be edited
// and stand down while it is read. The stylesheet disables them on any page
// marked as a reading surface, so the editable panel must not carry that mark
// anywhere around its text.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { localDocSession } from '../../lib/docLocalSession'
import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import { READING } from './doc/blocks/shapes'
import PanelDocView from './PanelDocView.vue'

import { setLocale, t } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

// jsdom lays nothing out; the editor measures text when it scrolls the caret into view.
const noRects = () => Object.assign([], { item: () => null }) as unknown as DOMRectList
Range.prototype.getClientRects ??= noRects
Range.prototype.getBoundingClientRect ??= () => new DOMRect()

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

function open(editable: boolean, text = TEXT) {
  render(PanelDocView, {
    props: docPanelProps({
      topic: { ...DOC_TOPIC },
      session: localDocSession(text, DOC_TOPIC.id),
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

const CHART = '开头一段。\n\n:::chart bar\n| 周 | 次数 |\n| --- | --- |\n| 第 1 周 | 1020 |\n:::'

/** The element `selector` names, once the editor has drawn it. */
function drawn(selector: string): Promise<HTMLElement> {
  return waitFor(() => {
    const el = document.querySelector<HTMLElement>(selector)
    expect(el).not.toBeNull()
    return el!
  })
}

describe('a chart in the document panel', () => {
  it('is selected as a whole when pressed, so Backspace removes the chart', async () => {
    open(true, CHART)
    const chart = await drawn('.doc-prose [data-block="chart"]')
    await fireEvent.mouseDown(chart.querySelector('.doc-chart__canvas')!, { button: 0 })
    await fireEvent.keyDown(document.querySelector('.doc-prose')!, { key: 'Backspace' })
    expect(document.querySelector('.doc-prose [data-block="chart"]')).toBeNull()
    expect(document.querySelector('.doc-prose')!.textContent).toContain('开头一段。')
  })

  it('at the end of the document leaves room for a line: a click under it starts one', async () => {
    open(true, CHART)
    const prose = await drawn('.doc-prose')
    await waitFor(() => expect(prose.lastElementChild?.getAttribute('data-block')).toBe('chart'))
    await fireEvent.mouseDown(prose, { button: 0, clientY: 50 })
    expect(prose.lastElementChild?.tagName).toBe('P')
    expect(prose.querySelector('[data-block="chart"]')).not.toBeNull()
  })
})
