// @vitest-environment jsdom
// The payload domains' failure and edge evidence (questions / answers / legal /
// attachments / knowledges, the happy paths of which live in
// `payloadDomainsContract.spec.ts`). For each domain the new payload stack is
// driven through the same refusal, edge page, lost network and empty-payload
// cases, and the sentence a real consumer would surface is checked.
//
// The consumers read `.message` (getErrorMessage / axiosErrorMessage /
// `error.message` — e.g. useQuestionInvitations, usePendingConsent,
// useAttachmentUploads, useTeamKnowledge), which the stack sets to the server's
// own sentence, so a swallowed refusal must not change it.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import {
  auth,
  contractEnvironment,
  envelope,
  newer,
  primary,
  refresh,
  replace,
  server,
} from './requestContract.spec.helper'

vi.mock('@/errorReporter', () => ({ reportError: vi.fn() }))

contractEnvironment()

let newErrorMessage: (error: unknown, fallback: string) => string
let consumerMessage: (error: unknown) => string | undefined

type Namespace = Record<string, (...args: unknown[]) => Promise<unknown>>
let modules: Record<string, Namespace>

beforeAll(async () => {
  ;({ axiosErrorMessage: newErrorMessage } = await import('./axiosHttp'))
  // getErrorMessage is what the question/answer consumers actually call: it
  // reads `error.message`, so the refusal sentence has to survive there too.
  ;({ getErrorMessage: consumerMessage } = await import('@/utils/errors'))
  modules = {
    questions: (await import('./questions')).QuestionApi as unknown as Namespace,
    answers: (await import('./answers')).AnswersApi as unknown as Namespace,
    legal: (await import('./legal')).LegalApi as unknown as Namespace,
    attachments: (await import('./attachments')).AttachmentsApi as unknown as Namespace,
    knowledges: (await import('./knowledges')).KnowledgesApi as unknown as Namespace,
  }
})

type Domain = {
  label: string
  origin: string
  method: 'get'
  readPath: string
  read: (api: Namespace) => Promise<unknown>
  command?: { method: 'post' | 'put' | 'delete'; path: string; call: (api: Namespace) => Promise<unknown> }
}

const domains: Domain[] = [
  {
    label: 'questions',
    origin: primary,
    method: 'get',
    readPath: '/questions/5',
    read: (api) => api.detail(5),
    command: { method: 'delete', path: '/questions/5/invitations/9', call: (api) => api.cancelInviteUser(5, 9) },
  },
  {
    label: 'answers',
    origin: primary,
    method: 'get',
    readPath: '/questions/5/answers',
    read: (api) => api.getAnswers(5),
    command: { method: 'put', path: '/questions/5/answers/9/favorite', call: (api) => api.favorite(5, 9) },
  },
  {
    label: 'legal',
    origin: primary,
    method: 'get',
    readPath: '/legal/documents/terms',
    read: (api) => api.getDocument('terms'),
  },
  {
    label: 'attachments',
    origin: primary,
    method: 'get',
    readPath: '/attachments/9',
    read: (api) => api.detail(9),
  },
  {
    label: 'knowledges',
    origin: newer,
    method: 'get',
    readPath: '/knowledge/9',
    read: (api) => api.getDetail(9),
    command: { method: 'delete', path: '/knowledge/9', call: (api) => api.deleteKnowledge(9) },
  },
]

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

describe('domain: structured refusals keep their meaning', () => {
  it.each(domains)('$label keeps the condition name, sentence and data', async (domain) => {
    server.use(http[domain.method](`${domain.origin}${domain.readPath}`, () => structuredRefusal(403)))
    const api = modules[domain.label]!
    const error = await domain.read(api).catch((failure) => failure)
    expect(error).toMatchObject({
      message: 'You cannot read this',
      code: 'AccessDeniedError',
      data: { reason: 'membership' },
    })
    // The sentence a real consumer would show is the server's own sentence.
    expect(newErrorMessage(error, 'fallback')).toBe('You cannot read this')
    expect(consumerMessage(error)).toBe('You cannot read this')
  })

  it.each(domains)('$label reads an edge page as a transport failure', async (domain) => {
    server.use(
      http[domain.method](
        `${domain.origin}${domain.readPath}`,
        () => new HttpResponse('<html>offline</html>', { status: 502 })
      )
    )
    const api = modules[domain.label]!
    const error = await domain.read(api).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
  })

  it.each(domains)('$label uses the caller fallback for a lost network', async (domain) => {
    server.use(http[domain.method](`${domain.origin}${domain.readPath}`, () => HttpResponse.error()))
    const api = modules[domain.label]!
    const error = await domain.read(api).catch((failure) => failure)
    expect(newErrorMessage(error, 'fallback')).toBe('fallback')
  })

  it.each(domains)('$label returns a falsy payload unchanged, never a command sentinel', async (domain) => {
    for (const value of [null, false, 0, '']) {
      server.use(http[domain.method](`${domain.origin}${domain.readPath}`, () => HttpResponse.json(envelope(value))))
      const api = modules[domain.label]!
      expect(await domain.read(api)).toEqual(value)
    }
  })

  it.each(domains.filter((domain) => domain.command))(
    '$label makes an empty command payload an explicit success',
    async (domain) => {
      const command = domain.command!
      server.use(http[command.method](`${domain.origin}${command.path}`, () => HttpResponse.json(envelope(null))))
      const api = modules[domain.label]!
      expect(await command.call(api)).toBe(true)
    }
  )

  it.each(domains.filter((domain) => domain.command))('$label keeps a refused command a throw', async (domain) => {
    const command = domain.command!
    server.use(
      http[command.method](`${domain.origin}${command.path}`, () =>
        HttpResponse.json({ code: 500, message: 'boom', data: null }, { status: 500 })
      )
    )
    const api = modules[domain.label]!
    const error = await command.call(api).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
  })
})

describe('knowledges: authentication edge cases', () => {
  // knowledges rides the newer origin; this section pins its 401 contract
  // (bearer, refresh-and-retry, redirect, give-up). The primary-origin domains
  // share one transport and their auth evidence lives in
  // `spacesFailureContract.spec.ts`.
  const payload = { knowledge: { id: 9 } }

  it('sends no bearer for a logged-out or empty-token caller', async () => {
    for (const [loggedIn, accessToken] of [
      [false, 'contract-token'],
      [true, ''],
    ] as const) {
      auth.loggedIn = loggedIn
      auth.accessToken = accessToken
      server.use(
        http.get(`${newer}/knowledge/9`, ({ request }) => {
          expect(request.headers.get('authorization')).toBeNull()
          return HttpResponse.json(envelope(payload))
        })
      )
      expect(await modules.knowledges!.getDetail!(9)).toEqual(payload)
    }
  })

  it('refreshes once and retries with the returned token', async () => {
    server.resetHandlers()
    auth.accessToken = 'contract-token'
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const authorization: (string | null)[] = []
    server.use(
      http.get(`${newer}/knowledge/9`, ({ request }) => {
        authorization.push(request.headers.get('authorization'))
        if (authorization.length === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(envelope(payload))
      })
    )
    expect(await modules.knowledges!.getDetail!(9)).toEqual(payload)
    expect(authorization).toEqual(['Bearer contract-token', 'Bearer refreshed-token'])
    expect(refresh).toHaveBeenCalledOnce()
  })

  it('redirects to sign-in when the refresh is rejected, without a retry', async () => {
    server.resetHandlers()
    let requests = 0
    server.use(
      http.get(`${newer}/knowledge/9`, () => {
        requests++
        return new HttpResponse(null, { status: 401 })
      })
    )
    refresh.mockReset().mockResolvedValue({ kind: 'rejected' })
    replace.mockReset()
    await expect(modules.knowledges!.getDetail!(9)).rejects.toBeInstanceOf(Error)
    expect(requests).toBe(1)
    expect(replace).toHaveBeenCalledWith('/account/signin')
  })

  it('stops after a refreshed request is still unauthorized', async () => {
    server.resetHandlers()
    let requests = 0
    server.use(
      http.get(`${newer}/knowledge/9`, () => {
        requests++
        return new HttpResponse(null, { status: 401 })
      })
    )
    refresh.mockReset().mockImplementation(async () => {
      auth.accessToken = 'refreshed-token'
      return { kind: 'ok', token: 'refreshed-token' }
    })
    const error = await modules.knowledges!.getDetail!(9).catch((failure) => failure)
    expect(error).toBeInstanceOf(Error)
    expect(requests).toBe(2)
    expect(refresh).toHaveBeenCalledOnce()
  })
})
