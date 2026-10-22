// 「邀请好友」：人说得出的几条——剩几个名额是总名额减去已用的；「首次采纳后再加名额」
// 只在还没加过时说；复制的注册链接打开就带着这个邀请码；每位好友走到哪一步看得见。
import type { MyInvites } from '@/types/invites'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Invites from './Invites.vue'

import { getMyInvites } from '@/api/invites'
import { setLocale, t } from '@/i18n'

vi.mock('@/api/invites', async (actual) => ({
  ...(await actual<typeof import('@/api/invites')>()),
  getMyInvites: vi.fn(),
}))
vi.mock('@/api/avatars', () => ({ getDefaultAvatarId: vi.fn().mockResolvedValue(1) }))
vi.mock('vuetify-sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }))

const base: MyInvites = {
  code: 'K7Q2M9',
  max_uses: 4,
  use_count: 1,
  unlocked: true,
  unlock_uses: 3,
  invite_credits: 150,
  month_credits: 150,
  month_cap: 1500,
  invitees: [],
}

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  setLocale('en')
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function open(invites: Partial<MyInvites>) {
  vi.mocked(getMyInvites).mockResolvedValue({ ...base, ...invites })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Invites },
      { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const view = render(Invites, { global: { plugins: [router, createVuetify({ components, directives })] } })
  await view.findByText('K7Q2M9')
  return view
}

describe('邀请好友', () => {
  it('剩余名额是总名额减去已用的', async () => {
    const view = await open({ max_uses: 4, use_count: 3 })
    expect(view.getByTestId('invite-seats').textContent).toBe(
      t('account.invites.code.remaining', { left: 1, total: 4 })
    )
  })

  it('还没首次采纳时提示能再加几个名额，加过以后不再提', async () => {
    const locked = await open({ unlocked: false, max_uses: 1, use_count: 0 })
    expect(locked.getByTestId('invite-unlock-hint').textContent).toContain('3')
    cleanup()

    const unlocked = await open({ unlocked: true })
    expect(unlocked.queryByTestId('invite-unlock-hint')).toBeNull()
  })

  it('复制的注册链接打开就带着这个邀请码', async () => {
    const view = await open({})
    await fireEvent.click(view.getByRole('button', { name: t('account.invites.code.copyLink') }))
    const link = new URL(writeText.mock.calls[0][0])
    expect(link.pathname).toBe('/account/signup')
    expect(link.searchParams.get('code')).toBe('K7Q2M9')
  })

  it('本月邀请所得对着每月上限写出来，进度条画到相应位置', async () => {
    const view = await open({ month_credits: 600, month_cap: 1500 })
    const month = view.getByTestId('invite-month')
    expect(month.textContent).toContain(t('account.invites.people.month', { n: '600', cap: '1,500' }))
    expect(month.querySelector<HTMLElement>('.invites__month-fill')!.style.width).toBe('40%')
  })

  it('复制邀请码复制的就是这个码', async () => {
    const view = await open({})
    await fireEvent.click(view.getByRole('button', { name: t('account.invites.code.copyCode') }))
    expect(writeText).toHaveBeenCalledWith('K7Q2M9')
  })

  it('已邀请算每一位好友，已采纳只算首次采纳了的', async () => {
    const friend = (handle: string, stage: 'registered' | 'onboarded' | 'activated' | 'lapsed') => ({
      handle,
      nickname: null,
      avatar_id: null,
      registered_at: '2026-10-10T00:00:00Z',
      stage,
      credits: stage === 'activated' ? 150 : 0,
    })
    const view = await open({
      invitees: [friend('a', 'activated'), friend('b', 'onboarded'), friend('c', 'lapsed'), friend('d', 'activated')],
    })
    expect(view.getByTestId('invite-count').textContent?.trim()).toBe('4')
    expect(view.getByTestId('invite-accepted').textContent?.trim()).toBe('2')
  })

  it('邀请所得额度只算邀请好友挣到的，不算自己被邀请时得的', async () => {
    const friend = (handle: string, credits: number) => ({
      handle,
      nickname: null,
      avatar_id: null,
      registered_at: '2026-10-10T00:00:00Z',
      stage: 'activated' as const,
      credits,
    })
    // 450 = 两位好友的 150 + 150，加上这个人自己被邀请时得的 50 + 100。
    const view = await open({ invite_credits: 450, invitees: [friend('a', 150), friend('b', 150), friend('c', 0)] })
    expect(view.getByTestId('invite-credits').textContent?.trim()).toBe(
      t('account.invites.people.creditsValue', { n: 300 })
    )
  })

  it('每位好友各自走到哪一步', async () => {
    const view = await open({
      invitees: [
        {
          handle: 'bob',
          nickname: 'Bob',
          avatar_id: null,
          registered_at: '2026-10-10T00:00:00Z',
          stage: 'activated',
          credits: 150,
        },
        {
          handle: 'dan',
          nickname: null,
          avatar_id: null,
          registered_at: '2026-10-10T00:00:00Z',
          stage: 'activated',
          credits: 0,
        },
        {
          handle: 'carol',
          nickname: null,
          avatar_id: null,
          registered_at: '2026-10-11T00:00:00Z',
          stage: 'lapsed',
          credits: 0,
        },
      ],
    })
    const row = (handle: string) => view.getByText(`@${handle}`).closest('li')!.textContent
    expect(row('bob')).toContain(t('account.invites.people.stage.activated', { n: 150 }))
    expect(row('carol')).toContain(t('account.invites.people.stage.lapsed'))
    expect(row('carol')).not.toContain(t('account.invites.people.stage.activatedNoCredits'))
  })

  it('过了当月上限激活的好友，不说得了额度', async () => {
    const view = await open({
      invitees: [
        {
          handle: 'dan',
          nickname: null,
          avatar_id: null,
          registered_at: '2026-10-10T00:00:00Z',
          stage: 'activated',
          credits: 0,
        },
      ],
    })
    const row = view.getByText('@dan').closest('li')!.textContent
    expect(row).toContain(t('account.invites.people.stage.activatedNoCredits'))
    expect(row).not.toMatch(/\+\d/)
  })
})
