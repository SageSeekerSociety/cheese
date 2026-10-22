// 「知是正在内测」那张表：每样额度的数都照接口给的写；方案只在开头那句提，没有按月数的方案那句不写数；
// 还没领体验额度的主按钮是领取，领过的只剩「知道了」。
import type { BetaCredits } from '@/types/creditOffers'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BetaNoticeDialog from './BetaNoticeDialog.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  // 模态框是一个 VOverlay，而 happy-dom 没有 visualViewport：不补上它挂不起来。
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const OFFER: BetaCredits = {
  credits: 500,
  claimed: false,
  feedback_credits: 100,
  plan_credits: 1000,
  invite_credits: 150,
  invitee_credits: 150,
}

function mount(offer: Partial<BetaCredits> = {}) {
  return render(BetaNoticeDialog, {
    props: { open: true, offer: { ...OFFER, ...offer } },
    global: { plugins: [i18n, createVuetify({ components, directives })] },
  })
}

const rowOf = (what: string) => screen.getByText(what).closest('.beta-notice__row')?.textContent ?? ''

describe('「知是正在内测」说明', () => {
  it('每样额度的数都照接口给的写', async () => {
    mount({ plan_credits: 2000, credits: 300, feedback_credits: 80, invite_credits: 120, invitee_credits: 90 })
    expect(screen.getByText(/除了你方案里每月 2,000 点的额度（每月 1 日重置）/)).toBeTruthy()
    expect(screen.queryByText('每月方案额度')).toBeNull()
    expect(rowOf('内测体验额度')).toContain('300 点')
    expect(rowOf('反馈被采纳')).toContain('80 点')
    expect(rowOf('邀请好友')).toContain('120 点')
    expect(rowOf('邀请好友')).toContain('好友第一次采纳芝士的交付后到账，好友自己最多也能得到 90 点')
  })

  it('方案没有按月的数（不限或按时间窗口计），开头那句就不写数', () => {
    mount({ plan_credits: null })
    expect(screen.getByText(/除了你方案里的额度，/)).toBeTruthy()
    expect(screen.getByText('内测体验额度')).toBeTruthy()
  })

  it('还没领体验额度：主按钮是领取，另有「稍后」', async () => {
    const ui = mount()
    await fireEvent.click(screen.getByRole('button', { name: '领取 500 点' }))
    expect(ui.emitted('claim')).toHaveLength(1)
    await fireEvent.click(screen.getByRole('button', { name: '稍后' }))
    expect(ui.emitted('dismiss')).toHaveLength(1)
  })

  it('领过了：不再给领取，只剩「知道了」，那一行写明已经领过', async () => {
    const ui = mount({ claimed: true })
    expect(screen.queryByRole('button', { name: /领取/ })).toBeNull()
    expect(rowOf('内测体验额度')).toContain('你已经领取过了')
    await fireEvent.click(screen.getByRole('button', { name: '知道了' }))
    expect(ui.emitted('dismiss')).toHaveLength(1)
  })
})
