// @vitest-environment jsdom
// The teams domain's request and payload contract: every method pins the wire
// request (method, URL, query, auth, body) and the payload the caller gets back.
// Teams rides the new origin (`axiosNewApi`), so the handler answers from
// `${newer}`. The failure and edge evidence — a structured refusal, a locked
// team reaching its handler, the unnamed sentence and the empty-command success
// bit — lives in the second half, driven through the real consumers rather than
// by asserting an exception type alone. The 401 refresh/redirect flow is in
// `teamsAuthContract.spec.ts`; the per-method happy path for the five shared
// domains is in `payloadDomainsContract.spec.ts`; spaces and the task board
// have their own.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import {
  type ContractCase,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  server,
} from './requestContract.spec.helper'

// The reporter is a side effect (a queued fetch to the run records), not a
// method under test: silence it so a refusal we deliberately send does not
// leave the suite with an unhandled request.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let teams: typeof import('./teams').TeamsApi
let handler: typeof import('@/services/ErrorHandler').default
let newErrorMessage: (error: unknown, fallback: string) => string
let toast: typeof import('vuetify-sonner').toast

beforeAll(async () => {
  ;({ TeamsApi: teams } = await import('./teams'))
  ;({ default: handler } = await import('@/services/ErrorHandler'))
  ;({ axiosErrorMessage: newErrorMessage } = await import('./axiosHttp'))
  ;({ toast } = await import('vuetify-sonner'))
})

// A join-link token and a handle go on the path URL-encoded, so pick values
// with characters that change under encoding.
const token = 'tok en&x'
const handle = 'my team'
const encodedToken = encodeURIComponent(token)
const encodedHandle = encodeURIComponent(handle)

// Every method of `TeamsApi`, with the arguments that reach the wire. URL and
// body are the contract; a param moves into `query`, a JSON body into `body`.
const cases: Record<string, ContractCase> = {
  search: {
    method: 'GET',
    path: '/teams',
    args: [{ query: '名字 & +', pageStart: 2, pageSize: 20 }],
    query: [
      ['query', '名字 & +'],
      ['pageStart', '2'],
      ['pageSize', '20'],
    ],
  },
  create: {
    method: 'POST',
    path: '/teams',
    args: [{ name: '小队', handle: 'squad', description: 'd', intro: 'i', avatarId: 3 }],
    body: { name: '小队', handle: 'squad', description: 'd', intro: 'i', avatarId: 3 },
  },
  update: {
    method: 'PATCH',
    path: '/teams/7',
    args: [7, { name: '小队', intro: 'i', avatarId: 3, visibility: 'public', handle: 'squad' }],
    body: { name: '小队', intro: 'i', avatarId: 3, visibility: 'public', handle: 'squad' },
  },
  del: { method: 'DELETE', path: '/teams/7', args: [7] },
  detail: { method: 'GET', path: '/teams/7', args: [7] },
  getJoinLink: { method: 'GET', path: '/teams/7/join-link', args: [7] },
  updateJoinLink: {
    method: 'PATCH',
    path: '/teams/7/join-link',
    args: [7, { approval: true }],
    body: { approval: true },
  },
  resetJoinLink: { method: 'POST', path: '/teams/7/join-link/reset', args: [7] },
  detailByJoinLink: { method: 'GET', path: `/team-invites/${encodedToken}`, args: [token] },
  joinByJoinLink: {
    method: 'POST',
    path: `/team-invites/${encodedToken}/join`,
    args: [token, { message: 'want in' }],
    body: { message: 'want in' },
  },
  join: { method: 'POST', path: '/teams/7/join', args: [7, { message: 'want in' }], body: { message: 'want in' } },
  detailByHandle: { method: 'GET', path: `/teams/by-handle/${encodedHandle}`, args: [handle] },
  getMyTeams: { method: 'GET', path: '/teams/my-teams', args: [] },
  list: {
    method: 'GET',
    path: '/teams',
    args: [{ pageSize: 20, pageStart: 2, sortBy: 'name', sortOrder: 'asc' }],
    query: [
      ['pageSize', '20'],
      ['pageStart', '2'],
      ['sortBy', 'name'],
      ['sortOrder', 'asc'],
    ],
  },
  addMember: {
    method: 'POST',
    path: '/teams/7/members',
    args: [7, { user_id: 9, role: 'MEMBER' }],
    body: { user_id: 9, role: 'MEMBER' },
  },
  updateMember: {
    method: 'PATCH',
    path: '/teams/7/members/9',
    args: [7, 9, { role: 'ADMIN' }],
    body: { role: 'ADMIN' },
  },
  removeMember: { method: 'DELETE', path: '/teams/7/members/9', args: [7, 9] },
  transferOwner: { method: 'PUT', path: '/teams/7/owner', args: [7, 9], body: { userId: 9 } },
  getMembers: { method: 'GET', path: '/teams/7/members', args: [7] },
  listTeamJoinRequests: {
    method: 'GET',
    path: '/teams/7/requests',
    args: [7, { status: 'PENDING', pageStart: 0, pageSize: 10 }],
    query: [
      ['status', 'PENDING'],
      ['pageStart', '0'],
      ['pageSize', '10'],
    ],
  },
  approveJoinRequest: { method: 'POST', path: '/teams/7/requests/9/approve', args: [7, 9] },
  rejectJoinRequest: { method: 'POST', path: '/teams/7/requests/9/reject', args: [7, 9] },
  createInvitation: {
    method: 'POST',
    path: '/teams/7/invitations',
    args: [7, { userId: 9, role: 'MEMBER', message: 'hi' }],
    body: { userId: 9, role: 'MEMBER', message: 'hi' },
  },
  listTeamInvitations: {
    method: 'GET',
    path: '/teams/7/invitations',
    args: [7, { status: 'PENDING', pageStart: 0, pageSize: 10 }],
    query: [
      ['status', 'PENDING'],
      ['pageStart', '0'],
      ['pageSize', '10'],
    ],
  },
  cancelInvitation: { method: 'DELETE', path: '/teams/7/invitations/9', args: [7, 9] },
  listMyJoinRequests: {
    method: 'GET',
    path: '/users/me/team-requests',
    args: [{ status: 'PENDING', pageStart: 0, pageSize: 10 }],
    query: [
      ['status', 'PENDING'],
      ['pageStart', '0'],
      ['pageSize', '10'],
    ],
  },
  cancelMyJoinRequest: { method: 'DELETE', path: '/users/me/team-requests/9', args: [9] },
  listMyInvitations: {
    method: 'GET',
    path: '/users/me/team-invitations',
    args: [{ status: 'PENDING', pageStart: 0, pageSize: 10 }],
    query: [
      ['status', 'PENDING'],
      ['pageStart', '0'],
      ['pageSize', '10'],
    ],
  },
  acceptInvitation: { method: 'POST', path: '/users/me/team-invitations/9/accept', args: [9] },
  declineInvitation: { method: 'POST', path: '/users/me/team-invitations/9/decline', args: [9] },
}

const lockedTasks = '任务甲, 任务乙'

const structuredRefusal = (status: number, name = 'AccessDeniedError') =>
  HttpResponse.json(
    {
      code: status,
      message: 'refused',
      data: null,
      error: { name, message: 'You cannot change this team', data: { teamId: 7, lockingTasks: lockedTasks } },
    },
    { status }
  )

describe('teams: every method keeps its request and payload', () => {
  it.each(Object.entries(cases))('%s preserves the request and returns the payload', async (name, test) => {
    await contractRequest(test, () =>
      (teams[name as keyof typeof teams] as (...args: unknown[]) => Promise<unknown>)(...test.args)
    )
  })
})

describe('teams: structured refusals keep their meaning', () => {
  it.each([400, 403, 404, 422, 500, 503])('%s keeps the condition name, sentence and data', async (status) => {
    server.use(http.get(`${newer}/teams/7`, () => structuredRefusal(status)))
    const error = await teams.detail(7).catch((failure) => failure)
    expect(error).toMatchObject({
      message: 'You cannot change this team',
      status,
      code: 'AccessDeniedError',
      data: { teamId: 7, lockingTasks: lockedTasks },
    })
    expect(newErrorMessage(error, 'fallback')).toBe('You cannot change this team')
  })

  // The real consumer: `ErrorHandler` names a refusal off the new `ApiError`
  // `.code`, reads the locking tasks off `.data`, and warns.
  it('routes a locked team to its handler and warns with the locking tasks', async () => {
    server.use(http.delete(`${newer}/teams/7`, () => structuredRefusal(403, 'TeamLockedError')))
    const error = await teams.del(7).catch((failure) => failure)
    expect(error).toMatchObject({ code: 'TeamLockedError', status: 403, data: { lockingTasks: lockedTasks } })

    // `toast.warning` is typed as the real function; at runtime `vuetify-sonner`
    // is mocked, so read the calls off a mock-typed alias.
    const warning = toast.warning as unknown as ReturnType<typeof vi.fn>
    warning.mockClear()
    // A registered refusal is swallowed: the caller sees `undefined`, not a
    // half-typed throw, and the user sees the warning.
    expect(await handler.withErrorHandling(() => teams.del(7))).toBeUndefined()
    expect(warning).toHaveBeenCalledTimes(1)
    const [sentence] = warning.mock.calls[0]
    expect(sentence).toContain(lockedTasks)
  })

  it('keeps an unnamed 403 sentence rather than replacing it with a generic one', async () => {
    server.use(
      http.get(`${newer}/teams/7`, () =>
        HttpResponse.json({ code: 403, message: 'Server sentence', data: null }, { status: 403 })
      )
    )
    const error = await teams.detail(7).catch((failure) => failure)
    expect(newErrorMessage(error, 'fallback')).toBe('Server sentence')
  })

  it('reads an edge page as a transport failure, including a 200 one', async () => {
    for (const status of [200, 502]) {
      server.use(http.get(`${newer}/teams/7`, () => new HttpResponse('<html>offline</html>', { status })))
      const error = (await teams.detail(7).catch((failure) => failure)) as Error
      expect(error).toBeInstanceOf(Error)
      expect(newErrorMessage(error, 'fallback')).toBe(error.message)
    }
  })

  it('uses the caller fallback for a lost network without retrying', async () => {
    let requests = 0
    server.use(
      http.get(`${newer}/teams/7`, () => {
        requests++
        return HttpResponse.error()
      })
    )
    const error = await teams.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(newErrorMessage(error, 'fallback')).toBe('fallback')
    expect(requests).toBe(1)
  })
})

describe('teams: a read never invents a command success', () => {
  it.each([null, false, 0, ''])('a payload read returns falsy %j unchanged', async (value) => {
    server.use(http.get(`${newer}/teams/7`, () => HttpResponse.json(envelope(value))))
    expect(await teams.detail(7)).toEqual(value)
  })

  it.each(['null', 'omitted', '204'])('keeps %s command success distinct from a caught failure', async (kind) => {
    server.use(
      http.delete(`${newer}/teams/7/members/9`, () =>
        kind === '204'
          ? new HttpResponse(null, { status: 204 })
          : HttpResponse.json(kind === 'null' ? envelope(null) : { code: 200, message: 'ok' })
      )
    )
    await expect(teams.removeMember(7, 9)).resolves.toBe(true)
  })

  it('routes an empty command through the handler as a success, not a false failure', async () => {
    server.use(http.delete(`${newer}/teams/7/members/9`, () => HttpResponse.json(envelope(null))))
    const success = vi.fn()
    const sentinel = await handler.withErrorHandling(() => teams.removeMember(7, 9), { showToast: false })
    if (sentinel !== undefined) success()
    expect(sentinel).toBe(true)
    expect(success).toHaveBeenCalledOnce()

    server.use(
      http.delete(`${newer}/teams/7/members/9`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const failure = vi.fn()
    const caught = await handler.withErrorHandling(() => teams.removeMember(7, 9), { showToast: false })
    if (caught !== undefined) failure()
    expect(caught).toBeUndefined()
    expect(failure).not.toHaveBeenCalled()
  })
})
