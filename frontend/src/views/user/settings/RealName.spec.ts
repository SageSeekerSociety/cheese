// The real-name page: nothing reaches the stored record unless the person
// saves, confirms who they are, and, for a delete, says yes first.
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SudoCancelledError, withSudo } from '@/utils/sudo'

import RealName from './RealName.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'

const STORED = {
  realName: '林知远',
  studentId: '2023200117',
  grade: '2023',
  major: '计算机科学与技术',
  className: '',
}
const MASKED = { ...STORED, realName: '林**', studentId: '2********7' }

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getRealNameInfo: vi.fn(),
    getPreciseRealNameInfo: vi.fn(),
    updateRealNameInfo: vi.fn(),
    deleteRealNameInfo: vi.fn(),
    getRealNameAccessLogs: vi.fn(),
  },
}))
vi.mock('@/utils/sudo', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/sudo')>()),
  withSudo: vi.fn(),
}))
vi.mock('@/services/account', () => ({ currentUserId: ref(7) }))
const confirmAnswer = vi.fn<() => Promise<boolean>>()
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: confirmAnswer }) }),
}))
vi.mock('@/composables/useChosenAvatar', () => ({
  ensureDefaultAvatarId: vi.fn(),
  isChosenAvatar: () => false,
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** Confirming who you are succeeds and runs the operation with a ticket. */
const confirmed = (_purpose: string, operation: (ticket: string) => Promise<unknown>) => operation('ticket')
/** The person backs out of confirming who they are. */
const backedOut = async () => {
  throw new SudoCancelledError()
}

/** Let whatever the last click started run to its end. */
const settled = () => new Promise((resolve) => setTimeout(resolve, 0))

// happy-dom does not submit a form from its submit button; a browser does.
const save = (view: Awaited<ReturnType<typeof renderPage>>) =>
  fireEvent.submit(view.getByRole('button', { name: 'Save' }).closest('form')!)

async function renderPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: RealName },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const view = render(RealName, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives })] },
  })
  await view.findByText(MASKED.realName)
  return view
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.getRealNameInfo).mockResolvedValue({ data: { hasIdentity: true, identity: MASKED } } as never)
  vi.mocked(UserApi.getPreciseRealNameInfo).mockResolvedValue({
    data: { hasIdentity: true, identity: STORED },
  } as never)
  vi.mocked(UserApi.updateRealNameInfo).mockResolvedValue({ data: { identity: STORED } } as never)
  vi.mocked(UserApi.deleteRealNameInfo).mockResolvedValue({} as never)
  vi.mocked(UserApi.getRealNameAccessLogs).mockResolvedValue({
    data: { logs: [], page: { pageSize: 0, hasMore: false, total: 0 } },
  } as never)
  vi.mocked(withSudo).mockImplementation(confirmed as never)
  confirmAnswer.mockResolvedValue(true)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('changing the record', () => {
  it('leaves the stored record as it was when the change is cancelled', async () => {
    const view = await renderPage()
    await fireEvent.click(view.getByRole('button', { name: 'Change' }))
    const name = (await view.findByLabelText(/^Name/)) as HTMLInputElement
    expect(name.value).toBe(STORED.realName)

    await fireEvent.update(name, '别的名字')
    await fireEvent.click(view.getByRole('button', { name: 'Cancel' }))

    expect(UserApi.updateRealNameInfo).not.toHaveBeenCalled()
    expect(view.queryByDisplayValue('别的名字')).toBeNull()
    expect(view.queryByText('别的名字')).toBeNull()
    expect(view.getByText(STORED.realName)).toBeTruthy()
  })

  it('saves what was entered, with a ticket for changing it', async () => {
    const view = await renderPage()
    await fireEvent.click(view.getByRole('button', { name: 'Change' }))
    await fireEvent.update(await view.findByLabelText(/^Class/), '计科 2 班')
    await save(view)

    await waitFor(() =>
      expect(UserApi.updateRealNameInfo).toHaveBeenCalledWith(7, { ...STORED, className: '计科 2 班' }, 'ticket')
    )
    expect(withSudo).toHaveBeenCalledWith('realname:update', expect.any(Function))
  })

  it('does not save without a name and a student ID', async () => {
    const view = await renderPage()
    await fireEvent.click(view.getByRole('button', { name: 'Change' }))
    await fireEvent.update(await view.findByLabelText(/^Student ID/), '   ')
    await save(view)

    expect(UserApi.updateRealNameInfo).not.toHaveBeenCalled()
  })

  it('stays on the masked record when confirming who you are is abandoned', async () => {
    vi.mocked(withSudo).mockImplementation(backedOut as never)
    const view = await renderPage()

    await fireEvent.click(view.getByRole('button', { name: 'Change' }))
    await waitFor(() => expect(withSudo).toHaveBeenCalled())
    await settled()

    expect(view.getByText(MASKED.realName)).toBeTruthy()
    expect(view.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(view.queryByText(STORED.realName)).toBeNull()
  })

  it('keeps the form as typed when confirming the save is abandoned', async () => {
    const view = await renderPage()
    await fireEvent.click(view.getByRole('button', { name: 'Change' }))
    const major = (await view.findByLabelText(/^Major/)) as HTMLInputElement
    await fireEvent.update(major, '数学')
    vi.mocked(withSudo).mockImplementation(backedOut as never)

    await save(view)

    await waitFor(() => expect(withSudo).toHaveBeenLastCalledWith('realname:update', expect.any(Function)))
    expect(UserApi.updateRealNameInfo).not.toHaveBeenCalled()
    expect(((await view.findByLabelText(/^Major/)) as HTMLInputElement).value).toBe('数学')
  })
})

describe('deleting the record', () => {
  it('deletes nothing unless the person says yes', async () => {
    confirmAnswer.mockResolvedValue(false)
    const view = await renderPage()

    await fireEvent.click(view.getByRole('button', { name: 'Delete real-name information' }))

    await waitFor(() => expect(confirmAnswer).toHaveBeenCalled())
    await settled()
    expect(withSudo).not.toHaveBeenCalled()
    expect(UserApi.deleteRealNameInfo).not.toHaveBeenCalled()
  })

  it('deletes with a ticket for deleting once the person says yes', async () => {
    const view = await renderPage()

    await fireEvent.click(view.getByRole('button', { name: 'Delete real-name information' }))

    await waitFor(() => expect(UserApi.deleteRealNameInfo).toHaveBeenCalledWith(7, 'ticket'))
    expect(withSudo).toHaveBeenCalledWith('realname:delete', expect.any(Function))
  })

  it('keeps the record when confirming who you are is abandoned', async () => {
    vi.mocked(withSudo).mockImplementation(backedOut as never)
    const view = await renderPage()

    await fireEvent.click(view.getByRole('button', { name: 'Delete real-name information' }))

    await waitFor(() => expect(withSudo).toHaveBeenCalled())
    await settled()
    expect(UserApi.deleteRealNameInfo).not.toHaveBeenCalled()
    expect(view.getByText(MASKED.realName)).toBeTruthy()
  })
})
