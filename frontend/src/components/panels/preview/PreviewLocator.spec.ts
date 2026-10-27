/**
 * 指出一处、写一句话的那条框。
 *
 * 守的是它弹出时不去抢键盘焦点。这条框是读者在文件里选中一段之后出来的，而页面上的
 * 选中只在焦点还留在正文上时才画出来：光标一进输入框，刚选中的那一段就不亮了，读者
 * 看不到自己指的是哪一段。要打字，点一下框。
 *
 * 修复前这条会红：框一出来，焦点就在那个输入框上。
 */
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewLocator from './PreviewLocator.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

const stubs = {
  VIcon: true,
  VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
}

it('指出一处以后去处由一个下拉定：默认发进对话，切到「仅留评论」就挂成评论', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '这里数字不对', canComment: true },
    global: { stubs },
  })
  const labels = () => [...ui.container.querySelectorAll('button')].map((b) => b.textContent?.trim())
  // 同一个选择不再摆两颗按钮：底部只有「加入待发」「发送」，去处在下拉里。
  expect(labels()).not.toContain('评论')
  const send = [...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '发送')!
  send.click()
  expect(ui.emitted().send).toHaveLength(1)

  await fireEvent.click(ui.getByTestId('locator-recipient'))
  await fireEvent.click(ui.getByTestId('locator-recipient-comment'))
  // 切到评论以后：主按钮改叫「评论」，「加入待发」收起（评论不进发给芝士的那一篮）。
  expect(labels()).toContain('评论')
  expect(labels()).not.toContain('加入待发')
  ;[...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '评论')!.click()
  expect(ui.emitted().comment).toHaveLength(1)
  expect(ui.emitted().send).toHaveLength(1)
})

it('给了位置就弹在那一处下面；下面放不下就翻到上面；左右夹在面板里', async () => {
  const anchor = { top: 100, bottom: 120, left: 600, panelWidth: 700, viewTop: 0, viewBottom: 800 }
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '', anchor },
    global: { stubs },
  })
  const card = ui.container.querySelector('.locator') as HTMLElement
  expect(card.classList.contains('locator--floating')).toBe(true)
  expect(card.dataset.side).toBe('below')
  expect(card.style.top).toBe('128px')
  // 面板 700 宽、卡片最宽 348、边距 12：左边最多到 340，不会伸出面板。
  expect(card.style.left).toBe('340px')

  await ui.rerender({ anchor: { ...anchor, top: 700, bottom: 720 } })
  expect(card.dataset.side).toBe('above')
  expect(parseFloat(card.style.top)).toBeLessThan(700)

  // 没有位置（键盘选中、那一处滚出视野、面板太窄）：退回贴在可见区底边。
  await ui.rerender({ anchor: null })
  expect(card.classList.contains('locator--floating')).toBe(false)
  expect(card.style.top).toBe('')
})

it('这一份文件挂不了评论时，只留「发送」一条出口', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'library/a.pdf', quote: '第一段' }, note: '这里', canComment: false },
    global: {
      stubs: {
        VIcon: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
  const labels = [...ui.container.querySelectorAll('button')].map((b) => b.textContent?.trim())
  expect(labels).toContain('发送')
  expect(labels).not.toContain('评论')
})

it('弹出时不去抢正文那边的焦点', async () => {
  const ui = render(PreviewLocator, {
    props: { target: null, note: '' },
    global: {
      stubs: {
        VIcon: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
  expect(ui.container.querySelector('.locator')).toBeNull()

  await ui.rerender({
    target: { label: '例行事务.pdf', quote: '第一段' },
    note: '',
  })
  const input = await waitFor(() => {
    const box = ui.container.querySelector('.locator__input') as HTMLTextAreaElement | null
    expect(box).toBeTruthy()
    return box as HTMLTextAreaElement
  })

  expect(document.activeElement).not.toBe(input)

  // 反面：这一条不是「焦点根本动不了」才绿的——框本身照样能聚焦，读者点一下就进来。
  input.focus()
  expect(document.activeElement).toBe(input)
})

it('支持把当前指认加入待发列表，并在待发条上一键全部发送', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '改这里', queuedCount: 0 },
    global: {
      stubs: {
        VIcon: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
  const queueBtn = [...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '加入待发')
  expect(queueBtn).toBeTruthy()
  queueBtn!.click()
  expect(ui.emitted().queue).toHaveLength(1)

  await ui.rerender({ target: null, note: '', queuedCount: 2 })
  expect(ui.container.textContent).toContain('待发批注 2 处')
  const sendAllBtn = [...ui.container.querySelectorAll('button')].find((b) => b.textContent?.trim() === '全部发送（2）')
  expect(sendAllBtn).toBeTruthy()
  sendAllBtn!.click()
  expect(ui.emitted().sendQueued).toHaveLength(1)
})

it('支持通过投递目标下拉切换为「仅留评论」并回车提交', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '留条评论', canComment: true },
    global: {
      stubs: {
        VIcon: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
  await fireEvent.click(ui.getByTestId('locator-recipient'))
  await fireEvent.click(ui.getByTestId('locator-recipient-comment'))
  const input = ui.container.querySelector('.locator__input') as HTMLTextAreaElement
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(ui.emitted().comment).toHaveLength(1)
  expect(ui.emitted().send).toBeUndefined()
})

it('拖选一段字：先只出「添加批注」，点了才展开整张卡；Esc 撤掉', async () => {
  const anchor = { top: 100, bottom: 120, left: 40, panelWidth: 700, viewTop: 0, viewBottom: 800 }
  const ui = render(PreviewLocator, {
    props: {
      target: { label: '第 1 页 · 二、时间安排', quote: '报名截止 11 月 8 日' },
      note: '',
      anchor,
      armFirst: true,
    },
    global: { stubs },
  })
  const arm = ui.getByTestId('locator-arm')
  expect(arm.textContent).toContain('添加批注')
  expect(ui.container.querySelector('.locator')).toBeNull()
  // 贴在选区左下 8px，不压选区。
  expect(arm.style.top).toBe('128px')
  expect(arm.style.left).toBe('40px')
  await fireEvent.click(arm)
  const field = await waitFor(() => ui.getByPlaceholderText('输入修改要求或批注内容…'))
  expect(document.activeElement).toBe(field)
  expect(ui.container.textContent).toContain('第 1 页 · 二、时间安排')

  // 换一处又先出按钮。
  await ui.rerender({ target: { label: '第 2 页', quote: '另一段' } })
  expect(ui.getByTestId('locator-arm')).toBeTruthy()
  await fireEvent.keyDown(ui.getByTestId('locator-arm'), { key: 'Escape' })
  expect(ui.emitted().cancel).toHaveLength(1)
})

it('没有位置可贴（键盘选中、面板太窄）时不先出按钮，直接出卡', () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '', anchor: null, armFirst: true },
    global: { stubs },
  })
  expect(ui.queryByTestId('locator-arm')).toBeNull()
  expect(ui.getByPlaceholderText('输入修改要求或批注内容…')).toBeTruthy()
})

it('两行输入：Shift+Enter 换行不发送，Enter 发送；卡底写着怎么发', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '第一行' },
    global: { stubs },
  })
  const field = ui.getByPlaceholderText('输入修改要求或批注内容…') as HTMLTextAreaElement
  expect(field.tagName).toBe('TEXTAREA')
  expect(field.getAttribute('rows')).toBe('2')
  await fireEvent.keyDown(field, { key: 'Enter', shiftKey: true })
  expect(ui.emitted().send).toBeUndefined()
  await fireEvent.keyDown(field, { key: 'Enter' })
  expect(ui.emitted().send).toHaveLength(1)
  expect(ui.container.textContent).toContain('「加入待发」可合并多处一起发')
})

it('去处菜单每一项都说清会发生什么', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '', canComment: true },
    global: { stubs },
  })
  await fireEvent.click(ui.getByTestId('locator-recipient'))
  const text = ui.getByRole('listbox').textContent ?? ''
  expect(text).toContain('发给芝士')
  expect(text).toContain('由芝士按这一处修改文件')
  expect(text).toContain('仅留评论')
  expect(text).toContain('不触发芝士修改')
})
