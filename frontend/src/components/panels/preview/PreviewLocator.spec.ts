/**
 * 指出一处、写一句话的那条框。
 *
 * 守的是它弹出时不去抢键盘焦点。这条框是读者在文件里选中一段之后出来的，而页面上的
 * 选中只在焦点还留在正文上时才画出来：光标一进输入框，刚选中的那一段就不亮了，读者
 * 看不到自己指的是哪一段。要打字，点一下框。
 *
 * 修复前这条会红：框一出来，焦点就在那个输入框上。
 */
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewLocator from './PreviewLocator.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

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
