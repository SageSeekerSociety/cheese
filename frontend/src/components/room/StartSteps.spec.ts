// 上手三步：打勾跟着服务端的事实走；三步都做完、或者这个人在哪亲手采纳过交付，就不再出现。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import StartSteps from './StartSteps.vue'

import i18n, { setLocale } from '@/i18n'

const none = { asked: false, answered: false, accepted: false, learned: false }

function mount(steps: typeof none | null) {
  return render(StartSteps, {
    props: { projectId: 'p1', steps },
    global: { plugins: [createVuetify({ components }), i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
})

describe('上手三步', () => {
  it('points at the first step not yet done, with how to do it', () => {
    const view = mount(none)
    expect(view.getByText('交给芝士第一件事').closest('li')?.getAttribute('aria-current')).toBe('step')
    expect(view.getByText(/直接按回车只发给项目成员/)).toBeTruthy()
  })

  it('moves on when the server says a step happened', async () => {
    const view = mount(none)
    await view.rerender({ projectId: 'p1', steps: { ...none, asked: true } })
    expect(
      view
        .getByText(/支线里回复计划/)
        .closest('li')
        ?.getAttribute('aria-current')
    ).toBe('step')
  })

  it('is gone once all three are done', () => {
    const view = mount({ asked: true, answered: true, accepted: true, learned: false })
    expect(view.queryByText('上手三步')).toBeNull()
  })

  it('is not shown to someone who has accepted a delivery before', () => {
    const view = mount({ ...none, learned: true })
    expect(view.queryByText('上手三步')).toBeNull()
  })

  it('folds into one line and stays folded', async () => {
    const view = mount(none)
    await fireEvent.click(view.getByRole('button', { name: '收起' }))
    expect(view.queryByText('交给芝士第一件事')).toBeNull()
    view.unmount()
    const again = mount(none)
    expect(again.getByRole('button', { name: /上手三步 · 0\/3/ })).toBeTruthy()
  })
})
