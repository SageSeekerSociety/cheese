// @vitest-environment jsdom
// The task board's failure and edge evidence: what a refusal, an edge page, a
// lost network, a 401 and an empty-payload command each do to the new payload
// stack. The happy-path request/response contract for every method lives in
// `taskBoardContract.spec.ts`; this file is only the cases a success handler
// cannot reach, kept separate so neither spec becomes a bin.
//
// The last section pins the notifications `del` command (same batch, pilot
// domain): a real 204 must resolve to the explicit success value `true`, never
// the `undefined` a caught failure maps to.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { auth, contractEnvironment, envelope, newer, refresh, replace, server } from './requestContract.spec.helper'

// The reporter is a side effect (a queued fetch to the run records), not a
// method under test: silence it so a swallowed failure does not leave the
// suite with an unhandled request.
vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let tasks: typeof import('./taskBoard').TaskBoardApi
let notifications: typeof import('./notifications').NotificationsApi
let handler: typeof import('@/services/ErrorHandler').default
let newErrorMessage: (error: unknown, fallback: string) => string

beforeAll(async () => {
  ;({ TaskBoardApi: tasks } = await import('./taskBoard'))
  ;({ NotificationsApi: notifications } = await import('./notifications'))
  ;({ default: handler } = await import('@/services/ErrorHandler'))
  ;({ axiosErrorMessage: newErrorMessage } = await import('./axiosHttp'))
})

const structuredRefusal = (status: number) =>
  HttpResponse.json(
    {
      code: status,
      message: 'refused',
      data: null,
      error: { name: 'AccessDeniedError', message: 'You cannot read this', data: { reason: 'membership' } },
    },
    { status }
  )

describe('task board: structured refusals keep their meaning', () => {
  it.each([400, 403, 404, 422, 500, 503])('%s keeps the condition name, sentence and data', async (status) => {
    server.use(http.get(`${newer}/tasks/7`, () => structuredRefusal(status)))
    const error = await tasks.detail(7).catch((failure) => failure)
    expect(error).toMatchObject({
      message: 'You cannot read this',
      status,
      code: 'AccessDeniedError',
      data: { reason: 'membership' },
    })
    expect(newErrorMessage(error, 'fallback')).toBe('You cannot read this')
  })

  it('keeps an unnamed 403 sentence rather than replacing it with a generic one', async () => {
    server.use(
      http.get(`${newer}/tasks/7`, () =>
        HttpResponse.json({ code: 403, message: 'Server sentence', data: null }, { status: 403 })
      )
    )
    const error = await tasks.detail(7).catch((failure) => failure)
    expect(newErrorMessage(error, 'fallback')).toBe('Server sentence')
  })

  it('reads an edge page as a transport failure, including a 200 one', async () => {
    for (const status of [200, 502]) {
      server.use(http.get(`${newer}/tasks/7`, () => new HttpResponse('<html>offline</html>', { status })))
      const error = (await tasks.detail(7).catch((failure) => failure)) as Error
      expect(error).toBeInstanceOf(Error)
      expect(newErrorMessage(error, 'fallback')).toBe(error.message)
    }
  })

  it('uses the caller fallback for a lost network without retrying', async () => {
    let requests = 0
    server.use(
      http.get(`${newer}/tasks/7`, () => {
        requests++
        return HttpResponse.error()
      })
    )
    const error = await tasks.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(newErrorMessage(error, 'fallback')).toBe('fallback')
    expect(requests).toBe(1)
  })
})

describe('task board: a read never invents a command success', () => {
  it.each([null, false, 0, ''])('a payload read returns falsy %j unchanged', async (value) => {
    server.use(http.get(`${newer}/tasks/7`, () => HttpResponse.json(envelope(value))))
    expect(await tasks.detail(7)).toEqual(value)
  })

  it('distinguishes a command sentinel from a caught failure for the caller', async () => {
    server.use(http.delete(`${newer}/tasks/42`, () => HttpResponse.json(envelope(null))))
    const success = vi.fn()
    const sentinel = await handler.withErrorHandling(() => tasks.del(42), { showToast: false })
    if (sentinel !== undefined) success()
    expect(sentinel).toBe(true)
    expect(success).toHaveBeenCalledOnce()

    server.use(
      http.delete(`${newer}/tasks/42`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const failure = vi.fn()
    const caught = await handler.withErrorHandling(() => tasks.del(42), { showToast: false })
    if (caught !== undefined) failure()
    expect(caught).toBeUndefined()
    expect(failure).not.toHaveBeenCalled()
  })

  it('lets the delete dialog branch on resolve vs throw, never on a falsy payload', async () => {
    // The real consumer (useTaskManagement.confirmDeleteTask) does exactly this:
    // await inside a try, success toast + navigate after; error toast in catch.
    // A 204/empty body must land in the success arm, not be read as a failure.
    server.use(http.delete(`${newer}/tasks/42`, () => HttpResponse.json(envelope(null))))
    const removed = vi.fn()
    try {
      await tasks.del(42)
      removed()
    } catch {
      /* the consumer shows the failure toast here */
    }
    expect(removed).toHaveBeenCalledOnce()

    server.use(
      http.delete(`${newer}/tasks/42`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const failed = vi.fn()
    try {
      await tasks.del(42)
      removed()
    } catch {
      failed()
    }
    expect(failed).toHaveBeenCalledOnce()
  })
})

describe('task board: authentication edge cases', () => {
  const detailUnauthorized = () => {
    let requests = 0
    server.use(
      http.get(`${newer}/tasks/7`, ({ request }) => {
        requests++
        expect(request.url.startsWith(`${newer}/tasks/7`)).toBe(true)
        return new HttpResponse(null, { status: 401 })
      })
    )
    return () => requests
  }

  it('sends no bearer for a logged-out or empty-token caller', async () => {
    for (const state of [
      { loggedIn: false, accessToken: 'contract-token' },
      { loggedIn: true, accessToken: '' },
    ]) {
      auth.loggedIn = state.loggedIn
      auth.accessToken = state.accessToken
      server.use(
        http.get(`${newer}/tasks/7`, ({ request }) => {
          expect(request.headers.get('authorization')).toBeNull()
          return HttpResponse.json(envelope({ task: { id: 7 } }))
        })
      )
      expect(await tasks.detail(7)).toEqual({ task: { id: 7 } })
    }
  })

  it('refreshes once and retries with the returned token', async () => {
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    // One handler for the whole exchange: the first answer is the 401, the
    // second carries the payload and must arrive with the new token.
    const authorization: (string | null)[] = []
    server.use(
      http.get(`${newer}/tasks/7`, ({ request }) => {
        expect(request.url.startsWith(`${newer}/tasks/7`)).toBe(true)
        authorization.push(request.headers.get('authorization'))
        if (authorization.length === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(envelope({ task: { id: 7 } }))
      })
    )
    expect(await tasks.detail(7)).toEqual({ task: { id: 7 } })
    expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
    expect(refresh).toHaveBeenCalledOnce()
  })

  // A renewal that fails — turned down or out of reach — is attempted once, and
  // that is all this layer owes. Ending the sign-in is no longer its job:
  // `lib/session.ts` announces a renewal the server turned down, and
  // `services/account.ts` drops the session and takes the person to a sign-in,
  // for both stacks (`account.sessionOver.spec.ts`).
  it('does not retry a failed renewal, and does not navigate to a sign-in itself', async () => {
    for (const outcome of [{ kind: 'rejected' as const }, { kind: 'unreachable' as const }]) {
      const count = detailUnauthorized()
      refresh.mockReset().mockResolvedValue(outcome)
      replace.mockReset()
      await expect(tasks.detail(7)).rejects.toBeInstanceOf(Error)
      expect(count()).toBe(1)
      expect(replace).not.toHaveBeenCalled()
    }
  })

  it('stops after a refreshed request is still unauthorized', async () => {
    const count = detailUnauthorized()
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const error = await tasks.detail(7).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(count()).toBe(2)
    expect(refresh).toHaveBeenCalledOnce()
  })
})

describe('notifications: an empty-payload command is an explicit success', () => {
  it('turns a real 204 delete into true, not the undefined failure sentinel', async () => {
    server.use(http.delete(`${newer}/notifications/9`, () => new HttpResponse(null, { status: 204 })))
    const result = await notifications.del(9)
    // A 204 carries no payload, but the command states the outcome as an
    // explicit `true` rather than the undefined a caught failure maps to.
    expect(result).toBe(true)
  })

  it('still returns a payload delete as the payload, not the sentinel', async () => {
    server.use(http.delete(`${newer}/notifications/9`, () => HttpResponse.json(envelope({ ok: 1 }))))
    expect(await notifications.del(9)).toEqual({ ok: 1 })
  })

  it('keeps a failed delete a throw, so the feed never removes the row on error', async () => {
    server.use(
      http.delete(`${newer}/notifications/9`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const error = await notifications.del(9).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
  })
})
