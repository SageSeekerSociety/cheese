/**
 * 攒着的那几处批注逐条列在输入框上方：哪一份文件的哪一处、引的哪句原文、要改什么。
 * 攒够一次发，芝士一轮改完——不是一条一条打断、每来一条重排一版。
 *
 * 这守的是「逐条列出来」这件事本身：只有个计数不算，读者得看清自己加的是哪几处。
 */
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PendingNotesTray from './PendingNotesTray.vue'

import { setLocale } from '@/i18n'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const stubs = {
  VIcon: true,
  VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
  BaseButton: { template: '<button v-bind="$attrs"><slot /></button>' },
}

function note(overrides: Record<string, unknown> = {}) {
  return {
    file: '招募方案.docx',
    version: '',
    where: '第 1 页 · 二、时间安排',
    quote: '报名截止 11 月 8 日',
    text: '把日期改成 11 月 10 日',
    audience: 'cheese' as const,
    message: '在 招募方案.docx 的 第 1 页 · 二、时间安排（「报名截止 11 月 8 日」）：把日期改成 11 月 10 日',
    ...overrides,
  }
}

function mount(api = usePendingNotes(() => true)) {
  const ui = render(PendingNotesTray, { global: { stubs, provide: { [PENDING_NOTES as symbol]: api } } })
  return { ui, api }
}

it('没有待发批注时整块不出现', () => {
  const { ui } = mount()
  expect(ui.queryByTestId('pending-notes-list')).toBeNull()
})

it('攒了几处就逐条列出来：位置、原文、要改什么、去处，各有各的', async () => {
  const { ui, api } = mount()
  api.add(note())
  api.add(
    note({
      file: '订单.xlsx',
      where: '汇总!B7',
      quote: '1,102',
      text: '这个数不对',
    })
  )
  await ui.rerender({})
  const rows = ui.getAllByTestId('pending-note')
  expect(rows).toHaveLength(2)
  const text = rows.map((row) => row.textContent ?? '')
  expect(text[0]).toContain('招募方案.docx')
  expect(text[0]).toContain('第 1 页 · 二、时间安排')
  expect(text[0]).toContain('「报名截止 11 月 8 日」')
  expect(text[0]).toContain('把日期改成 11 月 10 日')
  expect(text[0]).toContain('待发给任务芝士')
  expect(text[1]).toContain('订单.xlsx')
  expect(text[1]).toContain('这个数不对')
})

it('行上写「第 N 版」：攒着的那几处说的都是屏幕上这一版', async () => {
  const { ui, api } = mount()
  api.add(note({ version: '第 3 版' }))
  await ui.rerender({})
  expect(ui.getByTestId('pending-note').textContent).toContain('第 3 版')
})

it('标题那一行写清攒了几条', async () => {
  const { ui, api } = mount()
  api.add(note())
  api.add(note())
  await ui.rerender({})
  expect(ui.getByTestId('pending-notes-head').textContent).toContain('2 条待发批注')
})

it('单条能删掉，删完不再列它', async () => {
  const { ui, api } = mount()
  api.add(note({ text: '第一条' }))
  api.add(note({ text: '第二条' }))
  await ui.rerender({})
  expect(ui.getAllByTestId('pending-note')).toHaveLength(2)
  await fireEvent.click(ui.getAllByTestId('pending-note-remove')[0])
  await ui.rerender({})
  const rows = ui.getAllByTestId('pending-note')
  expect(rows).toHaveLength(1)
  expect(rows[0].textContent).toContain('第二条')
})

it('「全部发送」把攒着的这一批合成一条发出去，发完篮清空', async () => {
  const send = vi.fn(() => true)
  const api = usePendingNotes(send)
  const { ui } = mount(api)
  api.add(note({ message: '甲' }))
  api.add(note({ message: '乙' }))
  await ui.rerender({})
  await fireEvent.click(ui.getByTestId('pending-notes-send-all'))
  expect(send).toHaveBeenCalledTimes(1)
  expect(send.mock.calls[0][0]).toBe('甲\n\n乙')
  await ui.rerender({})
  expect(ui.queryByTestId('pending-notes-list')).toBeNull()
})

it('对话栏没收下时说一句，别让人以为按了没反应', async () => {
  const api = usePendingNotes(() => false)
  const { ui } = mount(api)
  api.add(note())
  await ui.rerender({})
  await fireEvent.click(ui.getByTestId('pending-notes-send-all'))
  await ui.rerender({})
  expect(ui.getByRole('alert').textContent).toContain('还留着')
  // 这一批留着，没被悄悄丢掉。
  expect(ui.getAllByTestId('pending-note')).toHaveLength(1)
})
