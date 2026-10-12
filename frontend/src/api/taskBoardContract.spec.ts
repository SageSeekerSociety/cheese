// @vitest-environment jsdom
// 题目（板上的赛题，`/tasks/…`）一域：把老模块 `@/network/api/tasks` 的 `TasksApi` 与
// 新模块 `./taskBoard` 的 `TaskBoardApi` 逐方法对拍 —— 发出去的浏览器请求（方法、地址与
// 查询、鉴权头、JSON/多分体正文）必须一字不差，而载荷契约从此换了：老栈回整只
// `{code,message,data}` 信封（调用方取 `.data`），新栈直接回 payload。这些端点在**新
// origin**（`newer`），与 `@/api/tasks`（房间任务，`/topics/{task}`）不是一回事。
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import {
  compareRequests,
  type ContractCase,
  contractEnvironment,
  envelope,
  newer,
  server,
  uploadFile,
} from './migrationContract.spec.helper'

contractEnvironment()

/** 一个方法一例。老新两个命名空间方法名相同，`name` 拿去两边取同一支。 */
const cases: { label: string; name: string; test: ContractCase }[] = [
  {
    label: 'confirmFromPdf',
    name: 'confirmFromPdf',
    test: {
      method: 'POST',
      path: '/tasks/publish/from-pdf/confirm',
      args: [{ drafts: [{ name: '题', intro: '', description: '', space: 7 }], taskOptions: {} }],
      body: { drafts: [{ name: '题', intro: '', description: '', space: 7 }], taskOptions: {} },
    },
  },
  {
    label: 'create',
    name: 'create',
    test: { method: 'POST', path: '/tasks', args: [{ name: '题' }], body: { name: '题' } },
  },
  {
    label: 'update',
    name: 'update',
    test: { method: 'PATCH', path: '/tasks/42', args: [42, { name: '新名' }], body: { name: '新名' } },
  },
  {
    label: 'listAttachments',
    name: 'listAttachments',
    test: { method: 'GET', path: '/tasks/42/attachments', args: [42] },
  },
  {
    label: 'uploadAttachment',
    name: 'uploadAttachment',
    // The file is built inside the test (after the File stub is in place); see the runner below.
    test: { method: 'POST', path: '/tasks/42/attachments', args: [42], fileFields: {} },
  },
  {
    label: 'removeAttachment',
    name: 'removeAttachment',
    test: { method: 'DELETE', path: '/tasks/42/attachments/9', args: [42, 9] },
  },
  { label: 'del', name: 'del', test: { method: 'DELETE', path: '/tasks/42', args: [42] } },
  {
    label: 'detail',
    name: 'detail',
    test: {
      method: 'GET',
      path: '/tasks/42',
      args: [42, { querySpace: true, queryTopics: true }],
      query: [
        ['querySpace', 'true'],
        ['queryTopics', 'true'],
      ],
    },
  },
  {
    label: 'list',
    name: 'list',
    test: {
      method: 'GET',
      path: '/tasks',
      args: [{ sort_by: 'createdAt', sort_order: 'desc', space: 7, topics: [1, 2] }],
      query: [
        ['sort_by', 'createdAt'],
        ['sort_order', 'desc'],
        ['space', '7'],
        ['topics', '1'],
        ['topics', '2'],
      ],
    },
  },
  {
    label: 'addParticipant',
    name: 'addParticipant',
    test: {
      method: 'POST',
      path: '/tasks/42/participants',
      args: [42, 7],
      query: [['member', '7']],
      body: { deadline: null },
    },
  },
  {
    label: 'join (user)',
    name: 'join',
    test: {
      method: 'POST',
      path: '/tasks/42/participations/user',
      args: [42, { deadline: null }],
      body: { deadline: null },
    },
  },
  {
    label: 'join (team)',
    name: 'join',
    test: {
      method: 'POST',
      path: '/tasks/42/participations/team',
      args: [42, { deadline: null }, 5],
      body: { deadline: null, teamId: 5 },
    },
  },
  { label: 'inheritance', name: 'inheritance', test: { method: 'GET', path: '/tasks/42/inheritance', args: [42] } },
  {
    label: 'getTaskTeams',
    name: 'getTaskTeams',
    test: { method: 'GET', path: '/tasks/42/teams', args: [42], query: [['filter', 'all']] },
  },
  {
    label: 'removeParticipant',
    name: 'removeParticipant',
    test: { method: 'DELETE', path: '/tasks/42/participants/11', args: [42, 11] },
  },
  {
    label: 'removeParticipantByMemberId',
    name: 'removeParticipantByMemberId',
    test: { method: 'DELETE', path: '/tasks/42/participants', args: [42, 7], query: [['member', '7']] },
  },
  {
    label: 'getParticipants',
    name: 'getParticipants',
    test: {
      method: 'GET',
      path: '/tasks/42/participants',
      args: [42],
      query: [['queryRealNameInfo', 'true']],
    },
  },
  {
    label: 'updateParticipant',
    name: 'updateParticipant',
    test: {
      method: 'PATCH',
      path: '/tasks/42/participants/11',
      args: [42, 11, { approved: 'APPROVED' }],
      body: { approved: 'APPROVED' },
    },
  },
  {
    label: 'updateParticipantByMemberId',
    name: 'updateParticipantByMemberId',
    test: {
      method: 'PATCH',
      path: '/tasks/42/participants',
      args: [42, 7, { approved: 'DISAPPROVED' }],
      query: [['member', '7']],
      body: { approved: 'DISAPPROVED' },
    },
  },
  {
    label: 'createSubmission',
    name: 'createSubmission',
    test: {
      method: 'POST',
      path: '/tasks/42/participants/11/submissions',
      args: [42, 11, [{ text: '答案' }]],
      body: [{ text: '答案' }],
    },
  },
  {
    label: 'updateSubmission',
    name: 'updateSubmission',
    test: {
      method: 'PATCH',
      path: '/tasks/42/participants/11/submissions/1',
      args: [42, 11, 1, [{ text: '改过的答案' }]],
      body: [{ text: '改过的答案' }],
    },
  },
  {
    label: 'listSubmissions',
    name: 'listSubmissions',
    test: {
      method: 'GET',
      path: '/tasks/42/participants/11/submissions',
      args: [42, 11, { sort_by: 'createdAt', sort_order: 'desc', pageSize: 10 }],
      query: [
        ['sort_by', 'createdAt'],
        ['sort_order', 'desc'],
        ['pageSize', '10'],
      ],
    },
  },
  {
    label: 'postSubmissionReview',
    name: 'postSubmissionReview',
    test: {
      method: 'POST',
      path: '/tasks/42/participants/11/submissions/88/review',
      args: [42, 11, 88, { accepted: true, score: 90, comment: '好' }],
      body: { accepted: true, score: 90, comment: '好' },
    },
  },
  {
    label: 'patchSubmissionReview',
    name: 'patchSubmissionReview',
    test: {
      method: 'PATCH',
      path: '/tasks/42/participants/11/submissions/88/review',
      args: [42, 11, 88, { accepted: false, score: 0, comment: '不行' }],
      body: { accepted: false, score: 0, comment: '不行' },
    },
  },
  {
    label: 'deleteSubmissionReview',
    name: 'deleteSubmissionReview',
    test: { method: 'DELETE', path: '/tasks/42/participants/11/submissions/88/review', args: [42, 11, 88] },
  },
  { label: 'resubmitTask', name: 'resubmitTask', test: { method: 'POST', path: '/tasks/42/resubmit', args: [42] } },
]

describe('题目 browser contract', () => {
  it.each(cases)('$label preserves the request and returns the payload', async ({ name, test }) => {
    const { TasksApi: old } = await import('@/network/api/tasks')
    const { TaskBoardApi: next } = await import('./taskBoard')
    const args = name === 'uploadAttachment' ? [42, uploadFile()] : test.args
    await compareRequests(
      test,
      () => (old[name as keyof typeof old] as (...args: unknown[]) => Promise<unknown>)(...args),
      () => (next[name as keyof typeof next] as (...args: unknown[]) => Promise<unknown>)(...args)
    )
  })

  // `previewFromPdf` 的多分体字段顺序是接口认的（spaceId 在 file 前），通用对拍器按
  // 「file 在前」拼期望，替不了它，单列一例。
  it('previewFromPdf keeps the multipart field order and returns the payload', async () => {
    const captured: unknown[] = []
    server.use(
      http.post(`${newer}/tasks/publish/from-pdf/preview`, async ({ request }) => {
        expect(request.headers.get('authorization')).toBe('Bearer contract-token')
        expect(request.headers.get('content-type')).toContain('multipart/form-data; boundary=')
        const form = await request.formData()
        const entries = await Promise.all(
          [...form.entries()].map(async ([key, value]) =>
            typeof value === 'string'
              ? [key, value]
              : [key, { name: value.name, type: value.type, bytes: [...new Uint8Array(await value.arrayBuffer())] }]
          )
        )
        captured.push(entries)
        return HttpResponse.json(envelope({ drafts: [] }, 201), { status: 201 })
      })
    )
    const { TasksApi: old } = await import('@/network/api/tasks')
    const { TaskBoardApi: next } = await import('./taskBoard')
    const data = { spaceId: 7, file: uploadFile(), templateIndex: 2, maxTasks: 3, categoryId: 4, submitterType: 'USER' }
    const oldRes = await old.previewFromPdf(data as never)
    const nextRes = await next.previewFromPdf(data as never)
    expect(oldRes.data).toEqual({ drafts: [] })
    expect(nextRes).toEqual({ drafts: [] })
    expect(captured).toHaveLength(2)
    expect(captured[0]).toEqual(captured[1])
    expect(captured[0]).toEqual([
      ['spaceId', '7'],
      ['file', { name: 'notes.pdf', type: 'application/pdf', bytes: [...new TextEncoder().encode('%PDF-1.7')] }],
      ['templateIndex', '2'],
      ['maxTasks', '3'],
      ['categoryId', '4'],
      ['submitterType', 'USER'],
    ])
  })
})
