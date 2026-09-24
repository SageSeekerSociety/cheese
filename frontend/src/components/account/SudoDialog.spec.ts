import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { startAuthentication } from '@simplewebauthn/browser'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SudoCancelledError, withSudo } from '@/utils/sudo'

import SudoDialog from './SudoDialog.vue'

import { setLocale } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'

const browser = vi.hoisted(() => ({ webAuthn: true }))

vi.mock('@/network/api/users', () => ({
  UserApi: {
    getMyAuthMethods: vi.fn(),
    getPasskeyAuthenticationOptions: vi.fn(),
    verifySudoPasskey: vi.fn(),
    verifySudoPassword: vi.fn(),
    verifySudoTOTP: vi.fn(),
    requestSudoEmailCode: vi.fn(),
    verifySudoEmailCode: vi.fn(),
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
  vi.mocked(UserApi.requestSudoEmailCode).mockResolvedValue({ data: { email: 'alice@example.com' } } as never)
  vi.mocked(UserApi.verifySudoEmailCode).mockResolvedValue({
    data: { verified: true, sudoTicket: 'ticket-4' },
  } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function accountWith(methods: { password?: boolean; passkey: boolean; twoFactor: boolean; emailCode?: boolean }) {
  vi.mocked(UserApi.getMyAuthMethods).mockResolvedValue({
    data: {
      password: methods.password ?? true,
      passkey: methods.passkey,
      twoFactor: methods.twoFactor,
      emailCode: methods.emailCode ?? false,
    },
  } as never)
}

async function codeField() {
  return waitFor(() => {
    const input = document.querySelector('.v-otp-input input')
    if (!input) throw new Error('no code field yet')
    return input
  })
}

/** Mount the dialog the way the app does, then run an operation behind it. */
async function ask(purpose: Parameters<typeof withSudo>[0] = 'password:change') {
  render(SudoDialog, { global: { plugins: [createVuetify({ components, directives })] } })
  const operation = vi.fn(async (ticket: string) => `done with ${ticket}`)
  const outcome = { settled: false }
  const result = withSudo(purpose, operation)
  result.then(
    () => (outcome.settled = true),
    () => (outcome.settled = true)
  )
  await screen.findByRole('heading', { name: 'Confirm it’s you' })
  return { result, operation, outcome }
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

  it('does not offer a password to an account without one', async () => {
    accountWith({ password: false, passkey: true, twoFactor: true })
    await ask()

    await screen.findByRole('button', { name: 'Confirm with a passkey' })
    expect(otherWays()).toEqual(['Enter a code from your authenticator app'])
  })

  it('leads with the code when that is the only way the account has', async () => {
    accountWith({ password: false, passkey: false, twoFactor: true })
    await ask()

    await waitFor(() => expect(document.querySelector('.v-otp-input input')).toBeTruthy())
    expect(screen.queryByLabelText('Password')).toBeNull()
    expect(otherWays()).toEqual([])
  })

  it('says so when the account has no way to confirm, and can still be closed', async () => {
    accountWith({ password: false, passkey: false, twoFactor: false })
    const { result } = await ask()

    expect(await screen.findByText('No way to confirm your identity is set up')).toBeTruthy()
    expect(screen.queryByLabelText('Password')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await expect(result).rejects.toBeInstanceOf(SudoCancelledError)
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
  it('runs the operation with the ticket the password earns, and returns its result', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { result, operation } = await ask('password:change')

    await fireEvent.update(await screen.findByLabelText('Password'), 'correct horse!1')
    expect(operation).not.toHaveBeenCalled()
    await submit(screen.getByLabelText('Password'))

    await expect(result).resolves.toBe('done with ticket-1')
    expect(operation).toHaveBeenCalledTimes(1)
    expect(UserApi.verifySudoPassword).toHaveBeenCalledWith('correct horse!1', 'password:change')
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Confirm it’s you' })).toBeNull())
  })

  it('runs the operation with the ticket a passkey earns', async () => {
    accountWith({ passkey: true, twoFactor: false })
    vi.mocked(UserApi.getPasskeyAuthenticationOptions).mockResolvedValue({
      data: { options: { challenge: 'c', rpId: 'example.test' } },
    } as never)
    vi.mocked(startAuthentication).mockResolvedValue({ id: 'assertion' } as never)
    vi.mocked(UserApi.verifySudoPasskey).mockResolvedValue({
      data: { verified: true, sudoTicket: 'ticket-3' },
    } as never)
    const { result } = await ask('passkey:delete')

    await fireEvent.click(await screen.findByRole('button', { name: 'Confirm with a passkey' }))

    await expect(result).resolves.toBe('done with ticket-3')
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
    const { result } = await ask('2fa:disable')

    await fireEvent.click(await screen.findByRole('button', { name: 'Enter a code from your authenticator app' }))
    const firstDigit = await waitFor(() => {
      const input = document.querySelector('.v-otp-input input')
      if (!input) throw new Error('no code field yet')
      return input
    })
    await fireEvent.paste(firstDigit, { clipboardData: { getData: () => '123456' } })

    await expect(result).resolves.toBe('done with ticket-2')
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

describe('confirming with a code mailed to the account', () => {
  it('lists it under the other ways, and choosing it sends the code', async () => {
    accountWith({ passkey: false, twoFactor: false, emailCode: true })
    await ask()

    await screen.findByLabelText('Password')
    await fireEvent.click(screen.getByRole('button', { name: 'Get a code by email' }))

    expect(await screen.findByText('We sent a code to alice@example.com')).toBeTruthy()
    expect(UserApi.requestSudoEmailCode).toHaveBeenCalledTimes(1)
  })

  it('is the way in for an account that has no other, sent only when asked', async () => {
    accountWith({ password: false, passkey: false, twoFactor: false, emailCode: true })
    const { result } = await ask('oauth:unbind')

    const send = await screen.findByRole('button', { name: 'Email me a code' })
    expect(UserApi.requestSudoEmailCode).not.toHaveBeenCalled()
    await fireEvent.click(send)
    await fireEvent.paste(await codeField(), { clipboardData: { getData: () => '246810' } })

    await expect(result).resolves.toBe('done with ticket-4')
    expect(UserApi.verifySudoEmailCode).toHaveBeenCalledWith('246810', 'oauth:unbind')
  })

  it('says a wrong code is wrong in the interface language, and stays open', async () => {
    accountWith({ password: false, passkey: false, twoFactor: false, emailCode: true })
    vi.mocked(UserApi.verifySudoEmailCode).mockRejectedValue(
      new BusinessError('Invalid or expired code', 401, {
        name: 'AuthenticationRequiredError',
        message: 'Invalid or expired code',
        data: { reason: 'invalid_email_code' },
      })
    )
    const { outcome } = await ask()
    setLocale('zh-CN')

    await fireEvent.click(await screen.findByRole('button', { name: '发送邮箱验证码' }))
    await fireEvent.paste(await codeField(), { clipboardData: { getData: () => '000000' } })

    expect(await screen.findByText('验证码不正确或已过期')).toBeTruthy()
    expect(outcome.settled).toBe(false)
  })

  it('says so when two-step verification was turned on meanwhile', async () => {
    accountWith({ password: false, passkey: false, twoFactor: false, emailCode: true })
    vi.mocked(UserApi.verifySudoEmailCode).mockRejectedValue(
      new BusinessError('An email code cannot confirm this account', 403, {
        name: 'ForbiddenError',
        message: 'An email code cannot confirm this account',
        data: { reason: 'email_code_unavailable' },
      })
    )
    const { outcome } = await ask()
    setLocale('zh-CN')

    await fireEvent.click(await screen.findByRole('button', { name: '发送邮箱验证码' }))
    await fireEvent.paste(await codeField(), { clipboardData: { getData: () => '135790' } })

    expect(await screen.findByText('此账号已开启两步验证，请使用其他方式')).toBeTruthy()
    expect(outcome.settled).toBe(false)
  })

  it('says how long to wait when a code was sent too recently', async () => {
    accountWith({ password: false, passkey: false, twoFactor: false, emailCode: true })
    vi.mocked(UserApi.requestSudoEmailCode).mockRejectedValue(
      new BusinessError('Please wait before requesting a new code', 400, {
        name: 'BadRequestError',
        message: 'Please wait before requesting a new code',
        data: { reason: 'email_code_too_soon', retryAfterSeconds: 20 },
      })
    )
    await ask()

    await fireEvent.click(await screen.findByRole('button', { name: 'Email me a code' }))

    expect(await screen.findByText('Request a new code in 20 seconds.')).toBeTruthy()
  })
})

describe('backing out', () => {
  it('does not run the operation, and tells the caller it was cancelled', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { result, operation } = await ask()

    await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    await expect(result).rejects.toBeInstanceOf(SudoCancelledError)
    expect(operation).not.toHaveBeenCalled()
    expect(UserApi.verifySudoPassword).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Confirm it’s you' })).toBeNull())
  })

  it('cancels on Escape', async () => {
    accountWith({ passkey: false, twoFactor: false })
    const { result, operation } = await ask()

    await fireEvent.keyDown(await screen.findByLabelText('Password'), { key: 'Escape' })

    await expect(result).rejects.toBeInstanceOf(SudoCancelledError)
    expect(operation).not.toHaveBeenCalled()
  })
})
