// @vitest-environment jsdom
// questions / answers / legal / attachments / knowledges / tags 六域：逐个方法把**旧栈**与
// **新栈**发出去的浏览器请求对拍（方法、地址与查询、鉴权头、JSON/多分体正文），并核对新的载
// 荷契约 —— JSON 调用直接给 payload，无载荷命令给显式成功值。knowledges 走新 origin
// （newer），其余五域走老 origin（primary）。比对的是浏览器可见的请求，不是 axios 配置或
// 包装源码。
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import {
  compareRequests,
  type ContractCase,
  contractEnvironment,
  envelope,
  newer,
  primary,
  server,
  uploadFile,
} from './migrationContract.spec.helper'

contractEnvironment()

const questions: Record<string, ContractCase> = {
  ask: {
    method: 'POST',
    path: '/questions',
    args: [{ title: '题', content: '正文', type: 1, topics: [1, 2], bounty: 100 }],
    body: { title: '题', content: '正文', type: 1, topics: [1, 2], bounty: 100 },
  },
  detail: { method: 'GET', path: '/questions/5', args: [5] },
  attitudeQuestion: {
    method: 'POST',
    path: '/questions/5/attitudes',
    args: [5, 'POSITIVE'],
    body: { attitude_type: 'POSITIVE' },
  },
  invitationRecommend: { method: 'GET', path: '/questions/5/invitations/recommendations', args: [5] },
  followQuestion: { method: 'POST', path: '/questions/5/followers', args: [5] },
  unfollowQuestion: { method: 'DELETE', path: '/questions/5/followers', args: [5] },
  inviteUser: {
    method: 'POST',
    path: '/questions/5/invitations',
    args: [5, 9],
    body: { user_id: 9 },
  },
  cancelInviteUser: { method: 'DELETE', path: '/questions/5/invitations/9', args: [5, 9] },
  getInvitaions: {
    method: 'GET',
    path: '/questions/5/invitations',
    args: [5, 2],
    query: [
      ['pageStart', '2'],
      ['pageSize', '20'],
    ],
  },
  addBounty: { method: 'PUT', path: '/questions/5/bounty', args: [5, 50], body: { bounty: 50 } },
  acceptAnswer: {
    method: 'PUT',
    path: '/questions/5/acceptance?answer_id=9',
    args: [5, 9],
    query: [['answer_id', '9']],
  },
}

const answers: Record<string, ContractCase> = {
  getAnswers: {
    method: 'GET',
    path: '/questions/5/answers',
    args: [5, 2],
    query: [
      ['pageStart', '2'],
      ['pageSize', '20'],
    ],
  },
  answerQuestion: {
    method: 'POST',
    path: '/questions/5/answers',
    args: [5, '我的回答'],
    body: { content: '我的回答' },
  },
  getAnswerDetail: { method: 'GET', path: '/questions/5/answers/9', args: [5, 9] },
  postAttitude: {
    method: 'POST',
    path: '/questions/5/answers/9/attitudes',
    args: [5, 9, 'NEGATIVE'],
    body: { attitude_type: 'NEGATIVE' },
  },
  favorite: { method: 'PUT', path: '/questions/5/answers/9/favorite', args: [5, 9] },
  unfavorite: { method: 'DELETE', path: '/questions/5/answers/9/favorite', args: [5, 9] },
}

const legal: Record<string, ContractCase> = {
  listDocuments: { method: 'GET', path: '/legal/documents', args: [] },
  getDocument: { method: 'GET', path: '/legal/documents/terms', args: ['terms'] },
  getPendingConsents: { method: 'GET', path: '/users/me/consents', args: [] },
  acceptDocuments: {
    method: 'POST',
    path: '/users/me/consents',
    args: [{ terms: '1.0', privacy: '2.0' }],
    body: { documents: { terms: '1.0', privacy: '2.0' } },
  },
}

const knowledges: Record<string, ContractCase> = {
  create: {
    method: 'POST',
    path: '/knowledge',
    args: [{ name: '设计规范', type: 'TEXT', content: '{}', teamId: 7 }],
    body: { name: '设计规范', type: 'TEXT', content: '{}', teamId: 7 },
  },
  list: {
    method: 'GET',
    path: '/knowledge',
    args: [{ teamId: 7, type: 'TEXT', labels: ['a', 'b'], pageStart: 0, sort_by: 'createdAt', projectId: undefined }],
    query: [
      ['teamId', '7'],
      ['type', 'TEXT'],
      ['labels', 'a'],
      ['labels', 'b'],
      ['pageStart', '0'],
      ['sort_by', 'createdAt'],
    ],
  },
  getDetail: { method: 'GET', path: '/knowledge/9', args: [9] },
  update: { method: 'PATCH', path: '/knowledge/9', args: [9, { name: '新名字' }], body: { name: '新名字' } },
  deleteKnowledge: { method: 'DELETE', path: '/knowledge/9', args: [9] },
}

const tags: Record<string, ContractCase> = {
  search: {
    method: 'GET',
    path: '/tags',
    args: ['设计'],
    query: [
      ['q', '设计'],
      ['pageSize', '20'],
    ],
  },
  create: { method: 'POST', path: '/tags', args: ['新话题'], body: { name: '新话题' } },
}

type Callable = (...args: unknown[]) => Promise<unknown>

describe('primary-origin domains browser contract', () => {
  it.each(Object.entries(questions))(
    'questions.%s preserves the request and returns the payload',
    async (name, test) => {
      const { QuestionApi: old } = await import('@/network/api/questions')
      const { QuestionApi: next } = await import('./questions')
      await compareRequests(
        test,
        () => (old[name as keyof typeof old] as Callable)(...test.args),
        () => (next[name as keyof typeof next] as Callable)(...test.args),
        primary
      )
    }
  )

  it.each(Object.entries(answers))('answers.%s preserves the request and returns the payload', async (name, test) => {
    const { AnswersApi: old } = await import('@/network/api/answers')
    const { AnswersApi: next } = await import('./answers')
    await compareRequests(
      test,
      () => (old[name as keyof typeof old] as Callable)(...test.args),
      () => (next[name as keyof typeof next] as Callable)(...test.args),
      primary
    )
  })

  it.each(Object.entries(legal))('legal.%s preserves the request and returns the payload', async (name, test) => {
    const { LegalApi: old } = await import('@/network/api/legal')
    const { LegalApi: next } = await import('./legal')
    await compareRequests(
      test,
      () => (old[name as keyof typeof old] as Callable)(...test.args),
      () => (next[name as keyof typeof next] as Callable)(...test.args),
      primary
    )
  })

  it.each(Object.entries(tags))('tags.%s preserves the request and returns the payload', async (name, test) => {
    const { TagsApi: old } = await import('@/network/api/tags')
    const { TagsApi: next } = await import('./tags')
    await compareRequests(
      test,
      () => (old[name as keyof typeof old] as Callable)(...test.args),
      () => (next[name as keyof typeof next] as Callable)(...test.args),
      primary
    )
  })

  it('legal.getDocument pins a version into the path when given one', async () => {
    const { LegalApi: old } = await import('@/network/api/legal')
    const { LegalApi: next } = await import('./legal')
    await compareRequests(
      { method: 'GET', path: '/legal/documents/terms/versions/1.0', args: [] },
      () => old.getDocument('terms', '1.0'),
      () => next.getDocument('terms', '1.0'),
      primary
    )
  })

  it.each([
    ['limits', { method: 'GET', path: '/attachments/limits', args: [] } as ContractCase],
    ['detail', { method: 'GET', path: '/attachments/9', args: [9] } as ContractCase],
  ])('attachments.%s preserves the request and returns the payload', async (name, test) => {
    const { AttachmentsApi: old } = await import('@/network/api/attachments')
    const { AttachmentsApi: next } = await import('./attachments')
    await compareRequests(
      test,
      () => (old[name as keyof typeof old] as Callable)(...test.args),
      () => (next[name as keyof typeof next] as Callable)(...test.args),
      primary
    )
  })

  it('attachments.upload sends the same named multipart fields and unwraps the 201 envelope', async () => {
    const { AttachmentsApi: old } = await import('@/network/api/attachments')
    const { AttachmentsApi: next } = await import('./attachments')
    const payload = { id: 42 }
    const seen: unknown[] = []
    server.use(
      http.post(`${primary}/attachments`, async ({ request }) => {
        expect(request.headers.get('authorization')).toBe('Bearer contract-token')
        expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
        const form = await request.formData()
        const entries = [...form.entries()]
        const files = entries.filter(([, value]) => typeof value !== 'string')
        const fields = entries.filter(([, value]) => typeof value === 'string')
        expect(files).toHaveLength(1)
        const file = files[0]![1] as File
        seen.push({
          fieldNames: Object.fromEntries(fields),
          fileKey: files[0]![0],
          name: file.name,
          type: file.type,
          bytes: [...new Uint8Array(await file.arrayBuffer())],
        })
        return HttpResponse.json(envelope(payload, 201), { status: 201 })
      })
    )
    const file = uploadFile()
    const oldResult = await old.upload({ type: 'file', file })
    const nextResult = await next.upload({ type: 'file', file })
    // 旧栈给整只信封，新栈直接给 payload。
    expect(oldResult.data).toEqual(payload)
    expect(nextResult).toEqual(payload)
    expect(seen).toHaveLength(2)
    expect(seen[1]).toEqual(seen[0])
    expect(seen[0]).toEqual({
      fieldNames: { type: 'file' },
      fileKey: 'file',
      name: 'notes.pdf',
      type: 'application/pdf',
      bytes: [...new TextEncoder().encode('%PDF-1.7')],
    })
  })
})

describe('new-origin domain browser contract', () => {
  it.each(Object.entries(knowledges))(
    'knowledges.%s preserves the request and returns the payload',
    async (name, test) => {
      const { KnowledgesApi: old } = await import('@/network/api/knowledges')
      const { KnowledgesApi: next } = await import('./knowledges')
      await compareRequests(
        test,
        () => (old[name as keyof typeof old] as Callable)(...test.args),
        () => (next[name as keyof typeof next] as Callable)(...test.args),
        newer
      )
    }
  )
})
