// 一条空间加入链接把人送到这一页时，走的是真路由记录：没登录先被路由上的
// `spaceSignInGuard` 送去登录，`?redirect=` 带着这条地址，登录完回来；登录着的人看到的
// 是地址里那个令牌指向的空间，按下确认才加入 —— 打开链接本身不加入。
import { RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const detail = vi.fn()
const join = vi.fn()
vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    detailByJoinLink: (...args: unknown[]) => detail(...args),
    joinByJoinLink: (...args: unknown[]) => join(...args),
  },
}))
// Where sign-in lands is the router's business; the sign-in page itself (and the
// providers it fetches) is not what this file tests.
vi.mock('@/views/account/SignIn.vue', () => ({ default: { render: () => null } }))

import i18n, { setLocale, t } from '@/i18n'
import router from '@/router'
import AccountService from '@/services/account'

const INVITE = {
  space: { id: 8, name: '操作系统 2026 秋', intro: '每周一道实验题', avatarId: null },
  joinApproval: false,
  joinStatus: 'none',
}

function mount() {
  render(RouterView, {
    global: { plugins: [createPinia(), router, createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  detail.mockReset().mockResolvedValue(INVITE)
  join.mockReset().mockResolvedValue({ ...INVITE, joinApproval: true, joinStatus: 'pending' })
})
afterEach(() => {
  cleanup()
  AccountService.loggedIn = false
})

describe('the join link in the address bar', () => {
  it('signed out: goes to sign-in and carries the link back as the redirect', async () => {
    AccountService.loggedIn = false
    mount()

    await router.push('/space-invites/tok-signed-out').catch(() => {})

    await waitFor(() => expect(router.currentRoute.value.name).toBe('SignIn'), { timeout: 20_000 })
    expect(router.currentRoute.value.query.redirect).toBe('/space-invites/tok-signed-out')
    expect(detail).not.toHaveBeenCalled()
  }, 30_000)

  it('signed in: names the space from the token, and joins only when the button is pressed', async () => {
    AccountService.loggedIn = true
    mount()

    await router.push('/space-invites/some-token').catch(() => {})

    await screen.findByText('操作系统 2026 秋', undefined, { timeout: 20_000 })
    expect(detail).toHaveBeenCalledWith('some-token')
    expect(screen.getByText(t('spaces.join.joinHint'))).toBeTruthy()
    expect(join).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByRole('button', { name: t('spaces.join.join') }))
    await waitFor(() => expect(join).toHaveBeenCalledWith('some-token'))
    expect(join).toHaveBeenCalledTimes(1)
  }, 30_000)
})
