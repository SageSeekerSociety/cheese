// The profile page: what it saves, when, and what a failure leaves behind.
import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { toast } from 'vuetify-sonner'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Profile from './Profile.vue'

import { setLocale } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { UserApi } from '@/network/api/users'
import AccountService from '@/services/account'

const DEFAULT_AVATAR = 1
const CHOSEN_AVATAR = 30

vi.mock('@/network/api/users', () => ({ UserApi: { updateUserInfo: vi.fn() } }))
vi.mock('@/network/api/avatars', () => ({
  AvatarsApi: { createAvatar: vi.fn(), getDefaultAvatarId: vi.fn() },
}))
vi.mock('@/services/account', async () => {
  const { reactive } = await import('vue')
  return { default: reactive({ loggedIn: true, user: null as unknown, updateUserInfo: vi.fn() }) }
})
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function signIn(avatarId = DEFAULT_AVATAR) {
  AccountService.user = reactive({
    id: 7,
    username: 'linzhiyuan',
    nickname: '林知远',
    intro: '做后端和数据管道',
    avatarId,
  }) as never
}

async function renderPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Profile },
      { path: '/users/:id', name: 'UserDefault', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  return render(Profile, { global: { plugins: [router, createVuetify({ components, directives })] } })
}

const nicknameField = (view: Awaited<ReturnType<typeof renderPage>>) =>
  view.getByLabelText('Display name') as HTMLInputElement
const introField = (view: Awaited<ReturnType<typeof renderPage>>) => view.getByLabelText('Bio') as HTMLTextAreaElement

async function chooseImage(view: Awaited<ReturnType<typeof renderPage>>, file: File) {
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

const png = (bytes = 1024) => new File([new Uint8Array(bytes)], 'me.png', { type: 'image/png' })

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(AvatarsApi.getDefaultAvatarId).mockResolvedValue({ data: { avatarId: DEFAULT_AVATAR } } as never)
  vi.mocked(UserApi.updateUserInfo).mockResolvedValue({} as never)
  signIn()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('nickname and bio', () => {
  it('cannot be saved until something has changed', async () => {
    const view = await renderPage()
    expect(view.queryByRole('button', { name: 'Save' })).toBeNull()

    await fireEvent.update(nicknameField(view), '林知远二号')

    const save = view.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
  })

  it('saves both fields, and the signed-in account shows them without a reload', async () => {
    const view = await renderPage()
    await fireEvent.update(nicknameField(view), '  林远  ')
    await fireEvent.update(introField(view), '研究检索增强')

    // happy-dom does not submit a form from its submit button; a browser does.
    await fireEvent.submit(nicknameField(view).closest('form')!)

    await waitFor(() =>
      expect(UserApi.updateUserInfo).toHaveBeenCalledWith(7, { nickname: '林远', intro: '研究检索增强' })
    )
    await waitFor(() => expect(AccountService.user).toMatchObject({ nickname: '林远', intro: '研究检索增强' }))
    await waitFor(() => expect(view.queryByRole('button', { name: 'Save' })).toBeNull())
  })

  it('Discard puts the saved values back and saves nothing', async () => {
    const view = await renderPage()
    await fireEvent.update(nicknameField(view), '别的名字')
    await fireEvent.update(introField(view), '别的简介')

    await fireEvent.click(view.getByRole('button', { name: 'Discard' }))

    expect(nicknameField(view).value).toBe('林知远')
    expect(introField(view).value).toBe('做后端和数据管道')
    await waitFor(() => expect(view.queryByRole('button', { name: 'Save' })).toBeNull())
    expect(UserApi.updateUserInfo).not.toHaveBeenCalled()
  })

  it.each([
    ['empty', ''],
    ['only spaces', '   '],
    ['only symbols', '!!!'],
    ['longer than 50 characters', '名'.repeat(51)],
  ])('an invalid nickname (%s) cannot be saved', async (_case, value) => {
    const view = await renderPage()
    await fireEvent.update(nicknameField(view), value)

    const save = view.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    // Pressing Enter in the field submits the form without the button.
    await fireEvent.submit(nicknameField(view).closest('form')!)

    expect(UserApi.updateUserInfo).not.toHaveBeenCalled()
    expect(AccountService.user).toMatchObject({ nickname: '林知远' })
  })
})

describe('avatar', () => {
  it('takes a chosen image at once, without saving an unfinished nickname', async () => {
    vi.mocked(AvatarsApi.createAvatar).mockResolvedValue({ data: { avatarId: 42 } } as never)
    const view = await renderPage()
    await fireEvent.update(nicknameField(view), '还没打完')

    await chooseImage(view, png())

    await waitFor(() => expect(UserApi.updateUserInfo).toHaveBeenCalledWith(7, { avatarId: 42 }))
    await waitFor(() => expect(AccountService.user).toMatchObject({ avatarId: 42, nickname: '林知远' }))
  })

  it.each([
    ['the upload', () => vi.mocked(AvatarsApi.createAvatar).mockRejectedValue(new Error('offline'))],
    [
      'the profile update',
      () => {
        vi.mocked(AvatarsApi.createAvatar).mockResolvedValue({ data: { avatarId: 42 } } as never)
        vi.mocked(UserApi.updateUserInfo).mockRejectedValue(new Error('offline'))
      },
    ],
  ])('keeps the current avatar when %s fails', async (_step, arrange) => {
    signIn(CHOSEN_AVATAR)
    arrange()
    const view = await renderPage()

    await chooseImage(view, png())

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(AccountService.user).toMatchObject({ avatarId: CHOSEN_AVATAR })
  })

  it.each([
    ['a file that is not a supported image', new File(['<svg/>'], 'me.svg', { type: 'image/svg+xml' })],
    ['an image over 2 MB', png(2 * 1024 * 1024 + 1)],
  ])('refuses %s without uploading it', async (_case, file) => {
    const view = await renderPage()

    await chooseImage(view, file)

    expect(AvatarsApi.createAvatar).not.toHaveBeenCalled()
    expect(UserApi.updateUserInfo).not.toHaveBeenCalled()
    expect(AccountService.user).toMatchObject({ avatarId: DEFAULT_AVATAR })
  })

  it('Remove puts the default avatar back', async () => {
    signIn(CHOSEN_AVATAR)
    const view = await renderPage()

    await fireEvent.click(await view.findByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(UserApi.updateUserInfo).toHaveBeenCalledWith(7, { avatarId: DEFAULT_AVATAR }))
    await waitFor(() => expect(AccountService.user).toMatchObject({ avatarId: DEFAULT_AVATAR }))
  })

  it('offers no Remove when there is no avatar of your own to remove', async () => {
    const view = await renderPage()
    await view.findByRole('button', { name: 'Change avatar' })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(view.queryByRole('button', { name: 'Remove' })).toBeNull()
  })
})
