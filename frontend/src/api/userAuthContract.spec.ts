// @vitest-environment jsdom
// userAuth 域（证明身份、进入登录态）的载荷契约：`UserAuthApi` 的每个方法都把**旧栈**与
// **新栈**发出去的浏览器请求对拍（方法、地址与查询、鉴权头、JSON 正文），并核对新的载荷
// 契约 —— JSON 调用直接给 payload，无载荷命令给显式成功值。整域走主 API 源（旧
// `ApiInstance`、新 `axiosApi`）。
//
// 三个不经过 axios 的助手（`redirectToOAuthLogin` 换浏览器地址、`createUserFromOAuth` /
// `bindOAuthToUser` 交一张隐藏表单）单独一组：它们没有请求可对拍，就分别核对落点与字段，
// 其中 bind 会跳过未填的字段、create 不会。
import { http, HttpResponse } from 'msw'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import {
  compareRequests,
  type ContractCase,
  contractEnvironment,
  envelope,
  primary,
  server,
} from './migrationContract.spec.helper'

vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let oldAuth: typeof import('@/network/api/users').UserApi
let newAuth: typeof import('./userAuth').UserAuthApi

beforeAll(async () => {
  ;({ UserApi: oldAuth } = await import('@/network/api/users'))
  ;({ UserAuthApi: newAuth } = await import('./userAuth'))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const call = (api: Record<string, unknown>, name: string, args: unknown[]) =>
  (api[name] as (...args: unknown[]) => Promise<unknown>)(...args)

const ada = { email: 'ada@example.com', code: '123456' }

const cases: Record<string, ContractCase> = {
  register: {
    method: 'POST',
    path: '/users',
    args: [{ username: 'ada', nickname: 'Ada', password: 'pw', email: 'ada@example.com', emailCode: '123456' }],
    body: { username: 'ada', nickname: 'Ada', password: 'pw', email: 'ada@example.com', emailCode: '123456' },
  },
  getRegistrationConfig: { method: 'GET', path: '/users/registration-config', args: [] },
  login: {
    method: 'POST',
    path: '/users/auth/login',
    args: [{ username: 'ada', password: 'pw' }],
    body: { username: 'ada', password: 'pw' },
  },
  requestSignInCode: {
    method: 'POST',
    path: '/users/auth/email-code',
    args: ['ada@example.com'],
    body: { email: 'ada@example.com' },
  },
  signInWithEmailCode: { method: 'POST', path: '/users/auth/email-code/verify', args: [ada], body: ada },
  sendEmailCode: {
    method: 'POST',
    path: '/users/verify/email',
    args: ['ada@example.com'],
    body: { email: 'ada@example.com' },
  },
  recoverPasswordRequest: {
    method: 'POST',
    path: '/users/recover/password/request',
    args: ['ada@example.com'],
    body: { email: 'ada@example.com' },
  },
  recoverPasswordVerify: {
    method: 'POST',
    path: '/users/recover/password/verify',
    args: [{ token: 'tok', password: 'pw' }],
    body: { token: 'tok', password: 'pw' },
  },
  getPasskeyAuthenticationOptions: {
    method: 'POST',
    path: '/users/auth/passkey/options',
    args: [7],
    body: { userId: 7 },
  },
  verifyPasskeyAuthentication: {
    method: 'POST',
    path: '/users/auth/passkey/verify',
    args: [{ id: 'cred-1' }],
    body: { response: { id: 'cred-1' } },
  },
  requestSudoTicket: {
    method: 'POST',
    path: '/users/auth/sudo',
    args: ['2fa:enable'],
    body: { purpose: '2fa:enable' },
  },
  // 四个 sudo 校验走同一条地址，靠 `method` 与 `credentials` 区分：这正是要钉住的契约。
  verifySudoPassword: {
    method: 'POST',
    path: '/users/auth/sudo',
    args: ['pw', 'realname:view'],
    body: { method: 'password', credentials: { password: 'pw' }, purpose: 'realname:view' },
  },
  verifySudoPasskey: {
    method: 'POST',
    path: '/users/auth/sudo',
    args: [{ id: 'assertion' }, 'passkey:add'],
    body: { method: 'passkey', credentials: { passkeyResponse: { id: 'assertion' } }, purpose: 'passkey:add' },
  },
  verifySudoTOTP: {
    method: 'POST',
    path: '/users/auth/sudo',
    args: ['123456', '2fa:disable'],
    body: { method: 'totp', credentials: { code: '123456' }, purpose: '2fa:disable' },
  },
  verifySudoEmailCode: {
    method: 'POST',
    path: '/users/auth/sudo',
    args: ['123456', 'password:change'],
    body: { method: 'email_code', credentials: { code: '123456' }, purpose: 'password:change' },
  },
  requestSudoEmailCode: { method: 'POST', path: '/users/me/sudo/email-code', args: [] },
  getMyAuthMethods: { method: 'GET', path: '/users/me/auth-methods', args: [] },
  verify2FA: {
    method: 'POST',
    path: '/users/auth/verify-2fa',
    args: [{ temp_token: 'temp', code: '123456', trust_device: true }],
    body: { temp_token: 'temp', code: '123456', trust_device: true },
  },
  logout: { method: 'POST', path: '/users/auth/logout', args: [] },
  startAppSignIn: {
    method: 'POST',
    path: '/users/auth/app-sign-in',
    args: ['challenge-1'],
    body: { challenge: 'challenge-1' },
  },
  finishAppSignIn: {
    method: 'POST',
    path: '/users/auth/app-sign-in/finish',
    args: ['code-1', 'verifier-1'],
    body: { code: 'code-1', verifier: 'verifier-1' },
  },
  getOAuthProviders: { method: 'GET', path: '/users/auth/oauth/providers', args: [] },
  verifyOAuth: {
    method: 'POST',
    path: '/users/auth/oauth/verify',
    args: [{ sessionId: 'sess-1', password: 'pw' }],
    body: { sessionId: 'sess-1', password: 'pw' },
  },
  getOAuthState: {
    // 令牌在地址里，`&` 与空格必须照旧编码。
    method: 'GET',
    path: '/users/auth/oauth/state',
    args: ['tok en&x'],
    query: [['token', 'tok en&x']],
  },
  sendOAuthEmailCode: {
    method: 'POST',
    path: '/users/auth/oauth/email/code',
    args: [{ stateToken: 'tok', email: 'ada@example.com' }],
    body: { stateToken: 'tok', email: 'ada@example.com' },
  },
  verifyOAuthEmail: {
    method: 'POST',
    path: '/users/auth/oauth/email/verify',
    args: [{ stateToken: 'tok', email: 'ada@example.com', code: '123456' }],
    body: { stateToken: 'tok', email: 'ada@example.com', code: '123456' },
  },
}

describe('userAuth migration: every method keeps its request and payload', () => {
  it.each(Object.entries(cases))('%s preserves the request and returns the payload', async (name, test) => {
    await compareRequests(
      test,
      () => call(oldAuth, name, test.args),
      () => call(newAuth, name, test.args),
      primary
    )
  })

  // 同一方法的第二种入参：邀请码只在给了的时候进正文。
  it('sendEmailCode carries the invite code only when one is given', async () => {
    const withInvite: ContractCase = {
      method: 'POST',
      path: '/users/verify/email',
      args: ['ada@example.com', 'INV-1'],
      body: { email: 'ada@example.com', inviteCode: 'INV-1' },
    }
    await compareRequests(
      withInvite,
      () => oldAuth.sendEmailCode('ada@example.com', 'INV-1'),
      () => newAuth.sendEmailCode('ada@example.com', 'INV-1'),
      primary
    )
  })
})

describe('userAuth migration: commands answer with an explicit success', () => {
  it.each(['logout', 'requestSignInCode'] as const)(
    '%s resolves true rather than the failure sentinel',
    async (name) => {
      const test = cases[name]
      const path = name === 'logout' ? '/users/auth/logout' : '/users/auth/email-code'
      server.use(http.post(`${primary}${path}`, () => HttpResponse.json(envelope(null))))
      expect(await call(newAuth, name, test.args)).toBe(true)
    }
  )
})

describe('userAuth migration: the helpers that leave axios', () => {
  it('redirectToOAuthLogin sends the browser to the same origin-qualified URL', () => {
    vi.stubGlobal('location', { href: '' })
    newAuth.redirectToOAuthLogin('github', 'st', 'offline')
    expect(window.location.href).toBe(`${primary}/users/auth/oauth/login/github?state=st&access_type=offline`)

    vi.stubGlobal('location', { href: '' })
    newAuth.redirectToOAuthLogin('google')
    expect(window.location.href).toBe(`${primary}/users/auth/oauth/login/google`)
  })

  it('createUserFromOAuth submits every field to the create endpoint', () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {})
    newAuth.createUserFromOAuth({
      stateToken: 'tok',
      username: 'ada',
      nickname: 'Ada',
      passwordMode: 'password',
      password: 'pw',
    })
    const form = document.body.lastElementChild as HTMLFormElement
    expect(form.method).toBe('post')
    expect(form.action).toBe(`${primary}/users/oauth/create`)
    expect([...form.elements].map((el) => [(el as HTMLInputElement).name, (el as HTMLInputElement).value])).toEqual([
      ['stateToken', 'tok'],
      ['username', 'ada'],
      ['nickname', 'Ada'],
      ['passwordMode', 'password'],
      ['password', 'pw'],
    ])
    expect(submit).toHaveBeenCalledOnce()
    form.remove()
  })

  it('bindOAuthToUser submits the filled fields and skips the empty ones', () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {})
    newAuth.bindOAuthToUser({ stateToken: 'tok', username: 'ada', password: 'pw' })
    const form = document.body.lastElementChild as HTMLFormElement
    expect(form.method).toBe('post')
    expect(form.action).toBe(`${primary}/users/oauth/bind`)
    expect([...form.elements].map((el) => [(el as HTMLInputElement).name, (el as HTMLInputElement).value])).toEqual([
      ['stateToken', 'tok'],
      ['username', 'ada'],
      ['password', 'pw'],
    ])
    expect(submit).toHaveBeenCalledOnce()
    form.remove()
  })
})
