// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { Editor } from '@tiptap/core'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { docExtensions, serializeDoc } from '../../../lib/docSchema'

import DocFormatToolbar from './DocFormatToolbar.vue'

import { setLocale } from '@/i18n'

let editor: Editor
beforeEach(() => {
  setLocale('zh-CN')
  editor = new Editor({
    element: document.createElement('div'),
    extensions: docExtensions(),
    content: '<p>前文 选中 后文</p>',
  })
  editor.commands.setTextSelection({ from: 4, to: 6 })
})
afterEach(() => {
  cleanup()
  editor.destroy()
})
function mount(disabled = false) {
  return render(DocFormatToolbar, {
    props: { editor, disabled, disabledReason: disabled ? '只读' : '' },
    global: { stubs: { VIcon: { template: '<span><slot /></span>' } } },
  })
}

describe('document formatting', () => {
  it('formats only the selected text and keeps the selection on mouse down', async () => {
    mount()
    const bold = screen.getByRole('button', { name: '加粗' })
    const event = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
    bold.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    await fireEvent.click(bold)
    expect(serializeDoc(editor)).toBe('前文 **选中** 后文')
    expect(editor.state.selection.from).toBe(4)
    expect(editor.state.selection.to).toBe(6)
    await waitFor(() => expect(bold.getAttribute('aria-pressed')).toBe('true'))
    await fireEvent.click(bold)
    expect(serializeDoc(editor)).toBe('前文 选中 后文')
  })

  it('tracks selection changes rather than leaving the previous active format', async () => {
    mount()
    await fireEvent.click(screen.getByRole('button', { name: '加粗' }))
    editor.commands.setTextSelection({ from: 1, to: 3 })
    await waitFor(() => expect(screen.getByRole('button', { name: '加粗' }).getAttribute('aria-pressed')).toBe('false'))
  })

  it('does not change read-only or loading content, even on synthetic activation', async () => {
    const view = mount(true)
    const original = serializeDoc(editor)
    for (const button of screen.getAllByRole('button')) await fireEvent.click(button)
    expect(serializeDoc(editor)).toBe(original)
    await view.rerender({ disabled: false, disabledReason: '' })
    editor.setEditable(false, false)
    await fireEvent.click(screen.getByRole('button', { name: '加粗' }))
    expect(serializeDoc(editor)).toBe(original)
  })

  it('scrolls only the toolbar to reveal keyboard focus in either direction', async () => {
    mount()
    const toolbar = screen.getByRole('toolbar')
    const buttons = screen.getAllByRole('button')
    toolbar.getBoundingClientRect = () => ({ left: 30, right: 130 }) as DOMRect
    buttons.forEach((button, index) => {
      button.getBoundingClientRect = () =>
        ({
          left: 38 + index * 30 - toolbar.scrollLeft,
          right: 66 + index * 30 - toolbar.scrollLeft,
        }) as DOMRect
    })
    buttons[0].focus()
    for (const key of ['End', 'ArrowLeft', 'ArrowRight', 'Home', 'ArrowLeft', 'ArrowRight']) {
      await fireEvent.keyDown(document.activeElement!, { key })
      const rect = (document.activeElement as HTMLElement).getBoundingClientRect()
      expect(rect.left).toBeGreaterThanOrEqual(30)
      expect(rect.right).toBeLessThanOrEqual(130)
      expect(window.scrollY).toBe(0)
    }
    expect(editor.state.selection.from).toBe(4)
    expect(editor.state.selection.to).toBe(6)
  })

  it('supports arrow, Home and End navigation with a single tab stop', async () => {
    mount()
    const buttons = screen.getAllByRole('button')
    buttons[0].focus()
    await fireEvent.keyDown(buttons[0], { key: 'ArrowRight' })
    expect(document.activeElement).toBe(buttons[1])
    await fireEvent.keyDown(buttons[1], { key: 'End' })
    expect(document.activeElement).toBe(buttons.at(-1))
    await fireEvent.keyDown(buttons.at(-1)!, { key: 'Home' })
    expect(document.activeElement).toBe(buttons[0])
    expect(buttons.filter((button) => button.tabIndex === 0)).toHaveLength(1)
    expect(editor.state.selection.from).toBe(4)
    expect(editor.state.selection.to).toBe(6)
  })
})
