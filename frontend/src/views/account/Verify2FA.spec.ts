// 开了 2FA 的人，过完第二步也要回到把他拦下来的那一页。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Verify2FA from './Verify2FA.vue'

import { UserApi } from '@/network/api/users'
import { forgetOAuthRedirect, stashOAuthRedirect } from '@/router/loginRedirect'

vi.mock('@/network/api/users', () => ({ UserApi: { verify2FA: vi.fn() } }))
vi.mock('@/services/account', () => ({ default: { login: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const blank = { template: '<div />' }

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.mocked(UserApi.verify2FA).mockReset()
})

async function open(query: Record<string, string>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/account/verify-2fa', name: 'Verify2FA', component: Verify2FA },
      { path: '/projects/:id', name: 'project', component: blank },
    ],
  })
  await router.push({ name: 'Verify2FA', query })
  const view = render(
    { template: '<router-view />' },
    {
      global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
    }
  )
  return { view, router }
}

// 填满六位即提交，和人输入时一样。
async function submitCode(view: ReturnType<typeof render>, code = '123456') {
  const inputs = view.container.querySelectorAll('input')
  for (const [i, digit] of [...code].entries()) {
    await fireEvent.focus(inputs[i])
    await fireEvent.update(inputs[i], digit)
  }
}

async function settle(router: ReturnType<typeof createRouter>, path: string) {
  await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe(path))
}

describe('过完 2FA 落在哪', () => {
  it('密码登录带来的来路', async () => {
    vi.mocked(UserApi.verify2FA).mockResolvedValue({ data: { accessToken: 't', user: {} } } as never)
    const { view, router } = await open({ token: 'ticket', redirect: '/projects/7' })

    await submitCode(view)

    await settle(router, '/projects/7')
  })

  it('OAuth 登录开了 2FA 由后端直接跳来，URL 上没有来路，用出站前存下的', async () => {
    stashOAuthRedirect('/projects/9')
    vi.mocked(UserApi.verify2FA).mockResolvedValue({ data: { accessToken: 't', user: {} } } as never)
    const { view, router } = await open({ token: 'ticket' })

    await submitCode(view)

    await settle(router, '/projects/9')
  })

  it('再次打开登录页后，上一次没走完的 OAuth 来路作废', async () => {
    stashOAuthRedirect('/projects/9')
    forgetOAuthRedirect()
    vi.mocked(UserApi.verify2FA).mockResolvedValue({ data: { accessToken: 't', user: {} } } as never)
    const { view, router } = await open({ token: 'ticket' })

    await submitCode(view)

    await settle(router, '/')
  })

  it('填满六位后再按回车，同一张票只交一次', async () => {
    let finish: (v: unknown) => void = () => {}
    vi.mocked(UserApi.verify2FA).mockReturnValue(new Promise((r) => (finish = r)) as never)
    const { view } = await open({ token: 'ticket', redirect: '/projects/7' })

    await submitCode(view)
    await fireEvent.submit(view.container.querySelector('form')!)
    finish({ data: { accessToken: 't', user: {} } })

    expect(UserApi.verify2FA).toHaveBeenCalledTimes(1)
  })

  it('验证码输错换票之后，来路还在', async () => {
    vi.mocked(UserApi.verify2FA)
      .mockRejectedValueOnce({
        error: { data: { reason: 'invalid_code', tempToken: 'ticket-2', attemptsRemaining: 4 } },
      })
      .mockResolvedValue({ data: { accessToken: 't', user: {} } } as never)
    const { view, router } = await open({ token: 'ticket', redirect: '/projects/7' })

    await submitCode(view)
    await vi.waitFor(() => expect(router.currentRoute.value.query.token).toBe('ticket-2'))
    await submitCode(view)

    await settle(router, '/projects/7')
  })
})
