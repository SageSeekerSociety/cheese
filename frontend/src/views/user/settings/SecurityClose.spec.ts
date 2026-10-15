// Closing the account from the security page: nothing is closed until the
// person types their own username and confirms, and backing out closes nothing.
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Security from './Security.vue'

import { UserApi } from '@/api/users'
import { setLocale } from '@/i18n'
import AccountService from '@/services/account'

vi.mock('@/api/users', () => ({
  UserApi: {
    get2FAStatus: vi.fn().mockResolvedValue({ enabled: false }),
    getUserPasskeys: vi.fn().mockResolvedValue({ passkeys: [] }),
    listSessions: vi.fn().mockResolvedValue({ sessions: [] }),
    closeAccount: vi.fn().mockResolvedValue({}),
  },
}))
vi.mock('@/api', () => ({
  listOAuthConnections: vi.fn().mockResolvedValue({ connections: [] }),
  deleteOAuthConnection: vi.fn(),
}))
vi.mock('@/services/account', () => ({
  currentUserId: ref(7),
  currentUserName: ref('ada'),
  default: { logout: vi.fn() },
}))
vi.mock('@/utils/sudo', () => ({
  SudoCancelledError: class SudoCancelledError extends Error {},
  withSudo: (_purpose: string, run: (ticket: string) => unknown) => run('ticket-1'),
}))
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: async () => true }) }),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

async function renderPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/settings', component: Security },
      { path: '/', component: { template: '<div />' } },
    ],
  })
  await router.push('/settings')
  await router.isReady()
  const view = render(Security, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
  return { view, router }
}

async function openDialog(view: Awaited<ReturnType<typeof renderPage>>['view']) {
  await fireEvent.click(view.getByRole('button', { name: 'Close account' }))
  return waitFor(() => view.getByLabelText('Username'))
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('closing the account', () => {
  it('cannot be confirmed until the username is typed', async () => {
    const { view } = await renderPage()
    const field = await openDialog(view)

    const confirm = () => view.getByRole('button', { name: 'Close for good' }) as HTMLButtonElement
    await fireEvent.update(field, 'someone-else')
    expect(confirm().disabled).toBe(true)
    await fireEvent.submit(field.closest('form')!)
    await fireEvent.update(field, 'ada')

    expect(confirm().disabled).toBe(false)
    expect(UserApi.closeAccount).not.toHaveBeenCalled()
  })

  it('backing out closes nothing', async () => {
    const { view } = await renderPage()
    const field = await openDialog(view)
    await fireEvent.update(field, 'ada')

    await fireEvent.click(view.getByRole('button', { name: 'Cancel' }))

    expect(UserApi.closeAccount).not.toHaveBeenCalled()
    expect(AccountService.logout).not.toHaveBeenCalled()
  })

  it('closes it once confirmed, and signs this tab out', async () => {
    const { view, router } = await renderPage()
    const field = await openDialog(view)
    await fireEvent.update(field, 'ada')

    await fireEvent.submit(field.closest('form')!)

    await waitFor(() => expect(UserApi.closeAccount).toHaveBeenCalledWith('ada', 'ticket-1'))
    await waitFor(() => expect(AccountService.logout).toHaveBeenCalled())
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/'))
  })
})
