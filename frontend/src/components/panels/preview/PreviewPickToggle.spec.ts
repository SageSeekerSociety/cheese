/**
 * 预览条上的「标一处」开关写着字。它曾经写成 `icon`，而那是纯图标按钮的写法：槽里的
 * 那几个字被吞掉，按钮只剩一颗方块图标，谁都认不出是干什么的。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import PreviewPickToggle from './PreviewPickToggle.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
afterEach(cleanup)

const vuetify = createVuetify({ components, directives })

it('按它自己的名字找得到它，按下就把开关交出去', async () => {
  const toggle = vi.fn()
  const ui = render(PreviewPickToggle, {
    props: { on: false },
    attrs: { onToggle: toggle },
    global: { plugins: [vuetify] },
  })
  const button = ui.getByRole('button', { name: '标一处' })
  expect(button.textContent).toContain('标一处')
  await fireEvent.click(button)
  expect(toggle).toHaveBeenCalledOnce()
})
