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

it('指出一处以后有两个出口：挂成评论，或者发进对话', async () => {
  const ui = render(PreviewLocator, {
    props: { target: { label: 'week-1.md', quote: '第一段' }, note: '这里数字不对', canComment: true },
    global: {
      stubs: {
        VIcon: true,
        VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
      },
    },
  })
  const buttons = [...ui.container.querySelectorAll('button')]
  // 两条出口各点一次：同一条输入框、同一句话，去处由按钮决定。
  const commentBtn = buttons.find((b) => b.textContent?.trim() === '评论')
  const askBtn = buttons.find((b) => b.textContent?.trim() === '发送')
  expect(commentBtn).toBeTruthy()
  expect(askBtn).toBeTruthy()
  commentBtn!.click()
  askBtn!.click()
  expect(ui.emitted().comment).toHaveLength(1)
  expect(ui.emitted().send).toHaveLength(1)
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
  // 按钮原样落成 <button>：这条读的是焦点，Vuetify 的按钮和图标在这一条里没有用。
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
    const box = ui.container.querySelector('.locator__input') as HTMLInputElement | null
    expect(box).toBeTruthy()
    return box as HTMLInputElement
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
  const select = ui.getByTestId('locator-recipient') as HTMLSelectElement
  await fireEvent.update(select, 'comment')
  const input = ui.container.querySelector('.locator__input') as HTMLInputElement
  await fireEvent.keyDown(input, { key: 'Enter' })
  expect(ui.emitted().comment).toHaveLength(1)
  expect(ui.emitted().send).toBeUndefined()
})
