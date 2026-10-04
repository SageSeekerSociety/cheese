// 第一次碰到时的那句说明：看过一次，这台浏览器上就不再出现；同一页上几处同样的说明
// 点掉一处一起收。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { resetFirstTimeHints } from '@/composables/useFirstTimeHint'

import FirstTimeHint from './FirstTimeHint.vue'

import i18n, { setLocale } from '@/i18n'

const Hint = FirstTimeHint as unknown as Component
const vuetify = createVuetify({ components, directives })

function mount(id = 'routine-draft') {
  return render(Hint, {
    props: { id },
    slots: { default: '草稿不会自己跑' },
    global: { plugins: [vuetify, i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  resetFirstTimeHints()
})

describe('第一次碰到时的说明', () => {
  it('没看过：把话说出来，带一颗「知道了」', () => {
    const { container, getByRole } = mount()
    expect(container.textContent).toContain('草稿不会自己跑')
    expect(getByRole('button', { name: '知道了' })).toBeTruthy()
  })

  it('点「知道了」：当场收起，换一页再来也不出现', async () => {
    const first = mount()
    await fireEvent.click(first.getByRole('button', { name: '知道了' }))
    expect(first.container.textContent).not.toContain('草稿不会自己跑')
    first.unmount()

    resetFirstTimeHints()
    const again = mount()
    expect(again.container.textContent).not.toContain('草稿不会自己跑')
  })

  it('同一页两处同样的说明：点掉一处，另一处一起收', async () => {
    const a = mount()
    const b = mount()
    await fireEvent.click(a.getAllByRole('button', { name: '知道了' })[0])
    expect(b.container.textContent).not.toContain('草稿不会自己跑')
  })

  it('看过一种，别的说明照旧出现', async () => {
    const a = mount('routine-draft')
    await fireEvent.click(a.getByRole('button', { name: '知道了' }))
    const b = mount('accept-card')
    expect(b.container.textContent).toContain('草稿不会自己跑')
  })
})
