import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { startAuthentication } from '@simplewebauthn/browser'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { confirmIdentity, SudoCancelledError } from '@/utils/sudo'

import SudoDialog from './SudoDialog.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'

const browser = vi.hoisted(() => ({ webAuthn: true }))

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getAuthMethods: vi.fn(),
    getPasskeyAuthenticationOptions: vi.fn(),
    verifySudoPasskey: vi.fn(),
    verifySudoPassword: vi.fn(),
    verifySudoTOTP: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({
  currentUserId: ref(7),
  currentUserName: ref('alice'),
}))
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: () => browser.webAuthn,
  startAuthentication: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  browser.webAuthn = true
  setLocale('en')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(UserApi.verifySudoPassword).mockResolvedValue({ data: { verified: true, sudoTicket: 'ticket-1' } } as never)
  vi.mocked(UserApi.verifySudoTOTP).mockResolvedValue({ data: { verified: true, sudoTicket: 'ticket-2' } } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function accountWith(methods: { passkey: boolean; twoFactor: boolean }) {
  vi.mocked(UserApi.getAuthMethods).mockResolvedValue({
    data: { supports_passkey: methods.passkey, supports_2fa: methods.twoFactor, requires_2fa: methods.twoFactor },
  } as never)
}

/** Mount the dialog the way the app does, then ask it for a confirmation. */
async function ask(purpose: Parameters<typeof confirmIdentity>[0] = 'password:change') {
  render(SudoDialog, { global: { plugins: [createVuetify({ components, directives })] } })
  const outcome = { settled: false }
  const ticket = confirmIdentity(purpose)
  ticket.then(
    () => (outcome.settled = true),
    () => (outcome.settled = true)
  )
  await screen.findByRole('heading', { name: 'Confirm it’s you' })
  return { ticket, outcome }
}

// happy-dom does not submit a form from a click on its submit button.
const submit = (field: HTMLElement) => fireEvent.submit(field.closest('form')!)

const otherWays = () => screen.queryAllByRole('button', { name: /^Enter / }).map((b) => b.textContent?.trim())

describe('what the dialog offers first', () => {
  it('leads with the passkey and lists the password and the code under it', async () => {
    accountWith({ passkey: true, twoFactor: true })
    await ask()

    expect(await screen.findByRole('button', { name: 'Confirm with a passkey' })).toBeTruthy()
    expect(otherWays()).toEqual(['Enter your password', 'Enter a code from your authenticator app'])
    expect(screen.queryByLabelText('Password')).toBeNull()
  })

  it('leads with the password when the account has no passkey', async () => {
    accountWith({ passkey: false, twoFactor: true })
    await ask()

    expect(await screen.findByLabelText('Password')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Confirm with a passkey' })).toBeNull()
    expect(otherWays()).toEqual(['Enter a code from your authenticator app'])
  })

  it('leads with the password when this browser cannot use a passkey', async () => {
    browser.webAuthn = false
    accountWith({ passkey: true, twoFactor: false })
    await ask()

    expect(await screen.findByLabelText('Password')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Confirm with a passkey' })).toBeNull()
    expect(otherWays()).toEqual([])
  })

  it('does not offer a code to an account without two-step verification', async () => {
    accountWith({ passkey: true, twoFactor: false })
    await ask()

    await screen.findByRole('button', { name: 'Confirm with a passkey' })
    expect(otherWays()).toEqual(['Enter your password'])
  })

  it('says what is being confirmed', async () => {
    accountWith({ passkey: false, twoFactor: false })
    await ask('passkey:add')

    expect(await screen.findByText('Before you add a passkey, you need to confirm your identity')).toBeTruthy()
  })
})

describe('moving between methods', () => {
  it('opens the chosen method and goes back to the list', async () => {
    accountWith({ passkey: true, twoFactor: true })
    await ask()

    await fireEvent.click(await screen.findByRole('button', { name: 'Enter your password' }))
    expect(await screen.findByLabelText('Password')).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Confirm with a passkey' })).toBeNull())

    await fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(await screen.findByRole('button', { name: 'Confirm with a passkey' })).toBeTruthy()
    expect(otherWays()).toEqual(['Enter your password', 'Enter a code from your authenticator app'])
  })
})

describe('confirming', () => {
  it('hands over the ticket the password earns for this purpose', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { ticket } = await ask('password:change')

    await fireEvent.update(await screen.findByLabelText('Password'), 'correct horse!1')
    await submit(screen.getByLabelText('Password'))

    await expect(ticket).resolves.toBe('ticket-1')
    expect(UserApi.verifySudoPassword).toHaveBeenCalledWith('correct horse!1', 'password:change')
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Confirm it’s you' })).toBeNull())
  })

  it('hands over the ticket a passkey earns for this purpose', async () => {
    accountWith({ passkey: true, twoFactor: false })
    vi.mocked(UserApi.getPasskeyAuthenticationOptions).mockResolvedValue({
      data: { options: { challenge: 'c', rpId: 'example.test' } },
    } as never)
    vi.mocked(startAuthentication).mockResolvedValue({ id: 'assertion' } as never)
    vi.mocked(UserApi.verifySudoPasskey).mockResolvedValue({
      data: { verified: true, sudoTicket: 'ticket-3' },
    } as never)
    const { ticket } = await ask('passkey:delete')

    await fireEvent.click(await screen.findByRole('button', { name: 'Confirm with a passkey' }))

    await expect(ticket).resolves.toBe('ticket-3')
    expect(UserApi.verifySudoPasskey).toHaveBeenCalledWith({ id: 'assertion' }, 'passkey:delete')
  })

  it('stays open when the passkey prompt is dismissed', async () => {
    accountWith({ passkey: true, twoFactor: false })
    vi.mocked(UserApi.getPasskeyAuthenticationOptions).mockResolvedValue({
      data: { options: { challenge: 'c', rpId: 'example.test' } },
    } as never)
    vi.mocked(startAuthentication).mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
    const { outcome } = await ask()

    await fireEvent.click(await screen.findByRole('button', { name: 'Confirm with a passkey' }))

    expect(await screen.findByText('Passkey confirmation was canceled')).toBeTruthy()
    expect(outcome.settled).toBe(false)
  })

  it('submits the authenticator code as soon as the sixth digit is in', async () => {
    accountWith({ passkey: false, twoFactor: true })
    const { ticket } = await ask('2fa:disable')

    await fireEvent.click(await screen.findByRole('button', { name: 'Enter a code from your authenticator app' }))
    const firstDigit = await waitFor(() => {
      const input = document.querySelector('.v-otp-input input')
      if (!input) throw new Error('no code field yet')
      return input
    })
    await fireEvent.paste(firstDigit, { clipboardData: { getData: () => '123456' } })

    await expect(ticket).resolves.toBe('ticket-2')
    expect(UserApi.verifySudoTOTP).toHaveBeenCalledWith('123456', '2fa:disable')
  })

  it('keeps the dialog open and says why when the password is wrong', async () => {
    accountWith({ passkey: false, twoFactor: false })
    vi.mocked(UserApi.verifySudoPassword).mockRejectedValue(new BusinessError('Incorrect password', 400))
    const { outcome } = await ask()

    await fireEvent.update(await screen.findByLabelText('Password'), 'wrong')
    await submit(screen.getByLabelText('Password'))

    expect(await screen.findByText('Incorrect password')).toBeTruthy()
    expect(outcome.settled).toBe(false)
  })

  it('lets a password manager fill in the password for this account', async () => {
    accountWith({ passkey: false, twoFactor: false })
    await ask()

    const password = await screen.findByLabelText('Password')
    expect(password.getAttribute('autocomplete')).toBe('current-password')
    const username = password.closest('form')!.querySelector<HTMLInputElement>('input[autocomplete="username"]')
    expect(username?.value).toBe('alice')
  })
})

describe('backing out', () => {
  it('ends the request as cancelled without verifying anything', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { ticket } = await ask()

    await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    await expect(ticket).rejects.toBeInstanceOf(SudoCancelledError)
    expect(UserApi.verifySudoPassword).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Confirm it’s you' })).toBeNull())
  })

  it('cancels on Escape', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { ticket } = await ask()

    await fireEvent.keyDown(await screen.findByLabelText('Password'), { key: 'Escape' })

    await expect(ticket).rejects.toBeInstanceOf(SudoCancelledError)
  })
})
