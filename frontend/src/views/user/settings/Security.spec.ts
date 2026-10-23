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

import { listOAuthConnections } from '@/api'
import { UserAuthApi } from '@/api/userAuth'
import { UserApi } from '@/api/users'
import { setLocale } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'

const CHROME_ON_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
const SAFARI_ON_IPHONE =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
// The desktop app's window: the system webview, which reports Edge on Windows.
const EDGE_ON_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0'

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

vi.mock('@/api/users', () => ({
  UserApi: {
    get2FAStatus: vi.fn().mockResolvedValue({ enabled: false }),
    getUserPasskeys: vi.fn().mockResolvedValue({ passkeys: [] }),
    listSessions: vi.fn(),
    revokeSession: vi.fn().mockResolvedValue({}),
    revokeOtherSessions: vi.fn().mockResolvedValue({ revokedCount: 1 }),
  },
}))
vi.mock('@/api', () => ({
  listOAuthConnections: vi.fn().mockResolvedValue({ connections: [] }),
  deleteOAuthConnection: vi.fn(),
}))
vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    getGithubAppIntent: vi.fn(),
    oauthLoginUrl: vi.fn(() => 'https://api.example.test/users/auth/oauth/login/github_app'),
  },
}))
vi.mock('@/lib/desktopApp', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/desktopApp')>()),
  goAuthorize: vi.fn(),
}))
vi.mock('@/services/account', () => ({
  currentUserId: ref(7),
  currentUserName: ref('ada'),
  default: { logout: vi.fn() },
}))
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
    sessions: [session('here', CHROME_ON_WINDOWS, true), session('phone', SAFARI_ON_IPHONE, false, 'oauth:github_app')],
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
    expect(view.getByText('Current')).toBeTruthy()
    expect(view.getByText('Password sign-in')).toBeTruthy()
    expect(view.getByText('GitHub sign-in')).toBeTruthy()
    expect(view.queryByText('Trusted')).toBeNull()
  })

  it('names the desktop app as the app, and a browser with the same User-Agent as that browser', async () => {
    // The two rows carry the same User-Agent — that is the point: only the way
    // each signed in tells the app from the Edge it is built on.
    vi.mocked(UserApi.listSessions).mockResolvedValue({
      sessions: [session('app', EDGE_ON_WINDOWS, true, 'app_sign_in'), session('edge', EDGE_ON_WINDOWS, false)],
    } as never)
    const view = await renderPage()

    await view.findByText('Desktop app on Windows')
    expect(view.getByText('Edge on Windows')).toBeTruthy()
    expect(view.getByText('Desktop app sign-in')).toBeTruthy()
    expect(view.getByText('Password sign-in')).toBeTruthy()
  })

  it('still names the desktop app when its webview names a system but no browser', async () => {
    // macOS's webview sends no Safari token, so only the system is readable.
    vi.mocked(UserApi.listSessions).mockResolvedValue({
      sessions: [
        session(
          'app',
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)',
          true,
          'app_sign_in'
        ),
        session('bare', 'CheeseDesktop/0.1.43', false, 'app_sign_in'),
      ],
    } as never)
    const view = await renderPage()

    await view.findByText('Desktop app on macOS')
    expect(view.getByText('Desktop app')).toBeTruthy()
  })

  it('marks a device trusted to skip two-step verification', async () => {
    vi.mocked(UserApi.listSessions).mockResolvedValue({
      sessions: [session('here', CHROME_ON_WINDOWS, true), session('phone', SAFARI_ON_IPHONE, false, 'password', true)],
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

    await fireEvent.click(view.getByRole('button', { name: 'Sign out everywhere else' }))

    await waitFor(() => expect(UserApi.revokeOtherSessions).toHaveBeenCalledTimes(1))
  })
})

function github(over: Record<string, unknown> = {}) {
  return {
    id: 5,
    providerId: 'github_app',
    providerName: 'GitHub',
    providerUserId: '42',
    connectedAt: '2026-08-16T00:00:00Z',
    login: 'octocat',
    tokenExpires: null,
    hasRefreshToken: true,
    ...over,
  }
}

describe('the linked GitHub account', () => {
  it('asks to renew the GitHub authorization when nothing can refresh it', async () => {
    vi.mocked(UserAuthApi.getGithubAppIntent).mockResolvedValue({ intent: 'account-ticket' })
    vi.mocked(listOAuthConnections).mockResolvedValue({
      connections: [github({ tokenExpires: '2026-01-01T00:00:00Z', hasRefreshToken: false })],
    })
    const { findByRole } = await renderPage()

    await fireEvent.click(await findByRole('button', { name: 'Renew GitHub authorization' }))

    await waitFor(() =>
      expect(goAuthorize).toHaveBeenCalledWith(
        'https://api.example.test/users/auth/oauth/login/github_app?intent=account-ticket'
      )
    )
  })

  it('asks nothing while a renewal token can still refresh it', async () => {
    vi.mocked(listOAuthConnections).mockResolvedValue({
      connections: [github({ tokenExpires: '2026-01-01T00:00:00Z', hasRefreshToken: true })],
    })
    const { findByText, queryByRole } = await renderPage()

    await findByText('@octocat')
    expect(queryByRole('button', { name: 'Renew GitHub authorization' })).toBeNull()
  })
})
