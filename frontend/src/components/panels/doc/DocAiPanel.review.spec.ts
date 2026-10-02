// @vitest-environment jsdom
import type { DocAiCard } from '../../../lib/docAiTypes'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import DocAiPanel from './DocAiPanel.vue'

import { setLocale } from '@/i18n'

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
  vi.stubGlobal(
    'visualViewport',
    Object.assign(new EventTarget(), { width: 1000, height: 600, offsetTop: 0, offsetLeft: 0 })
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
function proposal(): DocAiCard {
  return {
    request: {
      request_id: 'r',
      kind: 'propose',
      generation: 1,
      state: 'succeeded',
      proposal_id: 'p',
      answer: '解释',
      error: null,
    },
    proposal: {
      proposal_id: 'p',
      request_id: 'r',
      revision: 1,
      state: 'pending',
      document_id: 'doc',
      base_version: 4,
      selection: { node_id: 'n', start: 0, end: 4, exact_hash: 'a'.repeat(64) },
      replacement: '建议替换内容',
      answer: '解释',
      accepted_by: null,
      accepted_version: null,
    },
    context: { state: 'verified', question: '本次问题', original: '当时原文', scope: 'selection', baseVersion: 4 },
  }
}
function mount(cards = [proposal()]) {
  const accept = vi.fn()
  const close = vi.fn()
  const ui = render(DocAiPanel, {
    props: {
      cards,
      question: '尚未提交的输入',
      busy: false,
      error: '',
      selectionStatus: '',
      hasSelection: true,
      blocked: false,
      unknown: false,
      version: 4,
      docked: true,
      opened: true,
      onAccept: accept,
      onClose: close,
    },
    global: { plugins: [createVuetify({ components, directives })], stubs: { transition: false } },
  })
  return { ...ui, accept, close }
}
async function openReview() {
  const opener = screen.getByRole('button', { name: '查看修改' })
  opener.focus()
  await fireEvent.click(opener)
  return await screen.findByRole('dialog', { name: '查看修改' })
}
it('opens a full original/proposal comparison without writing and preserves the input when closed', async () => {
  const ui = mount()
  const dialog = await openReview()
  expect(within(dialog).getByText('当时原文', { exact: true })).toBeTruthy()
  expect(within(dialog).getByText('建议替换内容', { exact: true })).toBeTruthy()
  expect(ui.accept).not.toHaveBeenCalled()
  await fireEvent.click(within(dialog).getByRole('button', { name: '关闭对照' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(ui.accept).not.toHaveBeenCalled()
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('尚未提交的输入')
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: '查看修改' })))
  const reopened = await openReview()
  await fireEvent.click(within(reopened).getByRole('button', { name: '由我采纳并保存' }))
  expect(ui.accept).toHaveBeenCalledTimes(1)
  expect(ui.accept).toHaveBeenCalledWith('p')
})
it('one Escape closes only the review and returns to its opener without discarding the parent input', async () => {
  const ui = mount()
  const dialog = await openReview()
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
  await fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: '查看修改' })))
  expect(ui.close).not.toHaveBeenCalled()
  expect(ui.accept).not.toHaveBeenCalled()
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('尚未提交的输入')
})
it('does not steal focus from a new input when the review is closing', async () => {
  mount()
  const dialog = await openReview()
  await fireEvent.click(within(dialog).getByRole('button', { name: '关闭对照' }))
  const input = screen.getByRole('textbox')
  input.focus()
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(document.activeElement).toBe(input)
})
it('retiring the parent workspace does not return focus to a hidden review opener', async () => {
  const ui = mount()
  const dialog = await openReview()
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
  const opener = screen.getByRole('button', { name: '查看修改' })
  await ui.rerender({ opened: false })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(document.activeElement).not.toBe(opener)
  expect(ui.accept).not.toHaveBeenCalled()
})
it.each(['busy', 'unknown', 'blocked', 'version', 'context'] as const)(
  'cannot accept after the review becomes unsafe: %s',
  async (reason) => {
    const ui = mount()
    const dialog = await openReview()
    const patch =
      reason === 'version'
        ? { version: 5 }
        : reason === 'context'
          ? { cards: [{ ...proposal(), context: { state: 'invalid' as const } }] }
          : { [reason]: true }
    await ui.rerender(patch)
    const button = within(dialog).getByRole('button', { name: '由我采纳并保存' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    await fireEvent.click(button)
    expect(ui.accept).not.toHaveBeenCalled()
  }
)
it('retires the review if its proposal identity changes and never accepts the replacement by accident', async () => {
  const ui = mount()
  await openReview()
  const card = proposal()
  await ui.rerender({ cards: [{ ...card, proposal: { ...card.proposal!, proposal_id: 'other', revision: 2 } }] })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(ui.accept).not.toHaveBeenCalled()
})
