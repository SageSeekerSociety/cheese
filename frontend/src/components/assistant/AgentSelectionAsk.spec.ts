// 选中一段字，旁边出现「问芝士」，点了把这段字交出去；在可编辑的地方、项目文档里（标着
// data-agent-select="off"）不出，标着 "on" 的团队文档里照常出。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentSelectionAsk from './AgentSelectionAsk.vue'

import i18n, { setLocale } from '@/i18n'

function page(html: string): HTMLElement {
  const host = document.createElement('div')
  host.innerHTML = html
  document.body.append(host)
  return host
}

function select(el: Element) {
  const range = document.createRange()
  range.selectNodeContents(el)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  document.dispatchEvent(new MouseEvent('mouseup'))
}

function mount() {
  return render(AgentSelectionAsk, {
    props: { enabled: true, touch: false },
    global: { plugins: [createVuetify({ components }), i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.spyOn(Range.prototype, 'getBoundingClientRect').mockReturnValue({
    top: 200,
    bottom: 216,
    left: 100,
    right: 180,
    width: 80,
    height: 16,
    x: 100,
    y: 200,
    toJSON: () => ({}),
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  window.getSelection()?.removeAllRanges()
  document.body.innerHTML = ''
})

describe('问芝士 beside selected text', () => {
  it('shows beside a selection in a message and hands the text over', async () => {
    const view = mount()
    const host = page('<p id="m">首个字要等很久，所以超时设成 600 秒</p>')

    select(host.querySelector('#m')!)
    const ask = await view.findByRole('button', { name: /问芝士/ })
    await fireEvent.click(ask)

    expect(view.emitted('ask')).toEqual([['首个字要等很久，所以超时设成 600 秒']])
  })

  it('does not show where text is being edited, nor in a project document', async () => {
    const view = mount()
    const host = page(
      '<div contenteditable="true"><p id="e">正在写的字</p></div><div data-agent-select="off"><p id="d">项目文档</p></div>'
    )

    select(host.querySelector('#e')!)
    await new Promise((r) => setTimeout(r, 30))
    expect(view.queryByRole('button', { name: /问芝士/ })).toBeNull()

    select(host.querySelector('#d')!)
    await new Promise((r) => setTimeout(r, 30))
    expect(view.queryByRole('button', { name: /问芝士/ })).toBeNull()
  })

  it('shows in a team document, whose editor is marked as open to it', async () => {
    const view = mount()
    const host = page('<div data-agent-select="on"><div contenteditable="true"><p id="t">团队须知</p></div></div>')

    select(host.querySelector('#t')!)

    await waitFor(() => expect(view.getByRole('button', { name: /问芝士/ })).toBeTruthy())
  })
})
