// The security page's list of signed-in devices: what it shows, and that its
// buttons end the sign-ins they name.
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Security from './Security.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

const CHROME_ON_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
const SAFARI_ON_IPHONE =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'

function session(id: string, userAgent: string, current: boolean, loginMethod = 'password', trusted = false) {
  return {
    id,
    loginMethod,
    ipAddress: '203.0.113.7',
    userAgent,
    createdAt: '2026-09-01T08:00:00Z',
    lastActiveAt: '2026-09-20T08:00:00Z',
    current,
    trusted,
  }
}

vi.mock('@/network/api/users', () => ({
  UserApi: {
    get2FAStatus: vi.fn().mockResolvedValue({ data: { enabled: false } }),
    getUserPasskeys: vi.fn().mockResolvedValue({ data: { passkeys: [] } }),
    listSessions: vi.fn(),
    revokeSession: vi.fn().mockResolvedValue({}),
    revokeOtherSessions: vi.fn().mockResolvedValue({ data: { revokedCount: 1 } }),
  },
}))
vi.mock('@/api', () => ({
  listOAuthConnections: vi.fn().mockResolvedValue({ connections: [] }),
  deleteOAuthConnection: vi.fn(),
}))
vi.mock('@/services/account', () => ({ currentUserId: ref(7) }))
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: async () => true }) }),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

async function renderPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: Security }],
  })
  await router.push('/')
  await router.isReady()
  return render(Security, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.listSessions).mockResolvedValue({
    data: {
      sessions: [session('here', CHROME_ON_WINDOWS, true), session('phone', SAFARI_ON_IPHONE, false, 'oauth:github')],
    },
  } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('signed-in devices', () => {
  it('names each device, how it signed in, and marks the one in use', async () => {
    const view = await renderPage()

    await view.findByText('Chrome on Windows')
    expect(view.getByText('Safari on iOS')).toBeTruthy()
    expect(view.getByText('This device')).toBeTruthy()
    expect(view.getByText('Password sign-in')).toBeTruthy()
    expect(view.getByText('GitHub sign-in')).toBeTruthy()
    expect(view.queryByText('Trusted')).toBeNull()
  })

  it('marks a device trusted to skip two-step verification', async () => {
    vi.mocked(UserApi.listSessions).mockResolvedValue({
      data: {
        sessions: [
          session('here', CHROME_ON_WINDOWS, true),
          session('phone', SAFARI_ON_IPHONE, false, 'password', true),
        ],
      },
    } as never)
    const view = await renderPage()

    const phone = (await view.findByText('Safari on iOS')).closest('.srow') as HTMLElement
    expect(phone.textContent).toContain('Trusted')
    const here = view.getByText('Chrome on Windows').closest('.srow') as HTMLElement
    expect(here.textContent).not.toContain('Trusted')
  })

  it('signs out one other device, and never offers to sign out this one', async () => {
    const view = await renderPage()
    await view.findByText('Safari on iOS')

    const buttons = view.getAllByRole('button', { name: 'Sign out' })
    expect(buttons).toHaveLength(1)
    await fireEvent.click(buttons[0])

    await waitFor(() => expect(UserApi.revokeSession).toHaveBeenCalledWith('phone'))
    await waitFor(() => expect(UserApi.listSessions).toHaveBeenCalledTimes(2))
  })

  it('signs out every other device at once', async () => {
    const view = await renderPage()
    await view.findByText('Safari on iOS')

    await fireEvent.click(view.getByRole('button', { name: 'Sign out other devices' }))

    await waitFor(() => expect(UserApi.revokeOtherSessions).toHaveBeenCalledTimes(1))
  })
})
