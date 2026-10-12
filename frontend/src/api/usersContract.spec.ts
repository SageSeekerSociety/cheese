// @vitest-environment jsdom
// users 域（登录后管理自己的账号）的请求契约：`UserApi` 的每个方法都钉住它发出的浏览器请求
// （方法、地址与查询、鉴权头、刷新 cookie、JSON 正文），并核对载荷契约 —— JSON 调用直接给
// payload，无载荷命令给显式成功值。走主 API 源的路由一张表，五个实名方法是这里唯一走新 API
// 源（`axiosRequest(config, true)`）的部分，单独一张表。证明身份、进入登录态的那些接口在
// `userAuthContract.spec.ts`。
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import {
  type ContractCase,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  primary,
  server,
} from './requestContract.spec.helper'

// 上报是副作用（一条进运行记录的排队请求），不是被测方法：静音，免得我们故意送的拒绝
// 在套件里留下一条未处理请求。
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let newUsers: typeof import('./users').UserApi

beforeAll(async () => {
  ;({ UserApi: newUsers } = await import('./users'))
})

const call = (api: Record<string, unknown>, name: string, args: unknown[]) =>
  (api[name] as (...args: unknown[]) => Promise<unknown>)(...args)

// 走主 API 源（`axiosApi`）。方法与正文是契约；查询参数进 `query`。
const primaryCases: Record<string, ContractCase> = {
  getCurrentUser: { method: 'GET', path: '/users/me', args: [] },
  setLanguage: { method: 'PUT', path: '/users/me/language', args: ['en'], body: { language: 'en' } },
  setTimezone: {
    method: 'PUT',
    path: '/users/me/timezone',
    args: ['Asia/Shanghai'],
    body: { timezone: 'Asia/Shanghai' },
  },
  getUserInfo: { method: 'GET', path: '/users/7', args: [7] },
  updateUserInfo: { method: 'PATCH', path: '/users/7', args: [7, { nickname: '昵称' }], body: { nickname: '昵称' } },
  // 分页写在 GET 的 `data` 上 —— 浏览器按 XHR 规范丢掉 GET 的正文，它到不了线上。
  // 契约就是「没有查询参数、没有正文」，不顺手把它改成 params。
  getQuestionList: { method: 'GET', path: '/users/7/questions', args: [7, 1, 20] },
  getAnswerList: { method: 'GET', path: '/users/7/answers', args: [7, { pageStart: 0, pageSize: 20 }] },
  getPasskeyRegistrationOptions: {
    method: 'POST',
    path: '/users/7/passkeys/options',
    args: [7, 'sudo-1'],
    body: { sudoTicket: 'sudo-1' },
    credentials: 'include',
  },
  verifyPasskeyRegistration: {
    method: 'POST',
    path: '/users/7/passkeys',
    args: [7, { id: 'cred-1' }],
    body: { response: { id: 'cred-1' } },
    credentials: 'include',
  },
  dismissPasskeyPrompt: {
    method: 'POST',
    path: '/users/7/passkeys/prompt/dismiss',
    args: [7, true],
    body: { forever: true },
  },
  listSessions: { method: 'GET', path: '/users/me/sessions', args: [] },
  revokeSession: {
    method: 'DELETE',
    path: `/users/me/sessions/${encodeURIComponent('sess 1')}`,
    args: ['sess 1'],
  },
  revokeOtherSessions: { method: 'DELETE', path: '/users/me/sessions', args: [] },
  getUserPasskeys: { method: 'GET', path: '/users/7/passkeys', args: [7] },
  deletePasskey: {
    method: 'DELETE',
    path: '/users/7/passkeys/cred-1',
    args: [7, 'cred-1', 'sudo-1'],
    body: { sudoTicket: 'sudo-1' },
  },
  initializeTOTP: { method: 'POST', path: '/users/7/2fa/enable', args: [7, 'sudo-1'], body: { sudoTicket: 'sudo-1' } },
  enableTOTP: {
    method: 'POST',
    path: '/users/7/2fa/enable',
    args: [7, { code: '123456', secret: 'SECRET' }],
    body: { code: '123456', secret: 'SECRET' },
  },
  disableTOTP: { method: 'POST', path: '/users/7/2fa/disable', args: [7, 'sudo-1'], body: { sudoTicket: 'sudo-1' } },
  generateBackupCodes: {
    method: 'POST',
    path: '/users/7/2fa/backup-codes',
    args: [7, 'sudo-1'],
    body: { sudoTicket: 'sudo-1' },
  },
  get2FAStatus: { method: 'GET', path: '/users/7/2fa/status', args: [7] },
  changePassword: {
    method: 'PATCH',
    path: '/users/7/password',
    args: [7, { password: 'new-pass', sudoTicket: 'sudo-1' }],
    body: { password: 'new-pass', sudoTicket: 'sudo-1' },
  },
  sendAddEmailCode: {
    method: 'POST',
    path: '/users/me/email/code',
    args: ['ada@example.com'],
    body: { email: 'ada@example.com' },
  },
  addEmail: {
    method: 'POST',
    path: '/users/me/email',
    args: [{ email: 'ada@example.com', code: '123456' }],
    body: { email: 'ada@example.com', code: '123456' },
  },
}

// 实名五个方法走新 API 源（`axiosNewApi`）。
const identity = { realName: '张三', studentId: '2021', grade: '大一', major: '计算机', className: '1班' }
const newerCases: Record<string, ContractCase> = {
  getRealNameInfo: { method: 'GET', path: '/users/7/identity', args: [7], query: [['precise', 'false']] },
  getPreciseRealNameInfo: {
    method: 'GET',
    path: '/users/7/identity',
    args: [7, 'sudo-1'],
    query: [
      ['precise', 'true'],
      ['sudoTicket', 'sudo-1'],
    ],
  },
  updateRealNameInfo: {
    method: 'PUT',
    path: '/users/7/identity',
    args: [7, identity, 'sudo-1'],
    body: { ...identity, sudoTicket: 'sudo-1' },
  },
  deleteRealNameInfo: {
    method: 'DELETE',
    path: '/users/7/identity',
    args: [7, 'sudo-1'],
    body: { sudoTicket: 'sudo-1' },
  },
  getRealNameAccessLogs: {
    method: 'GET',
    path: '/users/7/identity/access-logs',
    args: [7, 0, 20],
    query: [
      ['pageStart', '0'],
      ['pageSize', '20'],
    ],
  },
}

describe('users: every method keeps its request and payload', () => {
  it.each(Object.entries(primaryCases))('%s preserves the primary-origin request and payload', async (name, test) => {
    await contractRequest(test, () => call(newUsers, name, test.args), primary)
  })

  it.each(Object.entries(newerCases))('%s preserves the newer-origin request and payload', async (name, test) => {
    await contractRequest(test, () => call(newUsers, name, test.args), newer)
  })
})

describe('users: commands answer with an explicit success', () => {
  it('a command with no payload resolves true rather than the failure sentinel', async () => {
    server.use(http.post(`${primary}/users/me/email/code`, () => HttpResponse.json(envelope(null))))
    expect(await newUsers.sendAddEmailCode('ada@example.com')).toBe(true)
  })

  it('a payload read returns a falsy payload unchanged', async () => {
    server.use(http.get(`${primary}/users/7`, () => HttpResponse.json(envelope(false))))
    expect(await newUsers.getUserInfo(7)).toBe(false)
  })
})

describe('users: refusals keep their meaning', () => {
  it('keeps a structured refusal name, sentence and data', async () => {
    server.use(
      http.get(`${primary}/users/7`, () =>
        HttpResponse.json(
          {
            code: 403,
            message: 'refused',
            data: null,
            error: { name: 'PrivacyDeniedError', message: 'You cannot read this profile', data: { userId: 7 } },
          },
          { status: 403 }
        )
      )
    )
    const next = await newUsers.getUserInfo(7).catch((error) => error)
    expect(next).toMatchObject({
      message: 'You cannot read this profile',
      status: 403,
      code: 'PrivacyDeniedError',
      data: { userId: 7 },
    })
  })

  it('reads an edge page as a transport failure', async () => {
    server.use(http.get(`${primary}/users/7`, () => new HttpResponse('<html>offline</html>', { status: 200 })))
    const next = await newUsers.getUserInfo(7).catch((error) => error)
    expect(next).toBeInstanceOf(Error)
  })
})
