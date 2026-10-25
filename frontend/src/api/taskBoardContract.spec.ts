// @vitest-environment jsdom
// 题目（板上的赛题，`/tasks/…`）一域：逐个方法钉住它发出去的浏览器请求（方法、地址与
// 查询、鉴权头、JSON/多分体正文），并核对载荷契约 —— JSON 调用直接给 payload，无载荷命令
// 给显式成功值。这些端点在**新 origin**（`newer`），与 `@/api/tasks`（房间任务，
// `/topics/{task}`）不是一回事。钉的是浏览器可见的请求，不是 axios 配置或包装源码。
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import {
  type ContractCase,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  server,
} from './requestContract.spec.helper'

contractEnvironment()

/** 一个方法一例。`name` 是 `TaskBoardApi` 上的方法名，`test.args` 原样传给它。 */
const cases: { label: string; name: string; test: ContractCase }[] = [
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
      body: {},
    },
  },
  {
    label: 'join (user)',
    name: 'join',
    test: {
      method: 'POST',
      path: '/tasks/42/participations/user',
      args: [42, { agreeMentors: true }],
      body: { agreeMentors: true },
    },
  },
  {
    label: 'join (team)',
    name: 'join',
    test: {
      method: 'POST',
      path: '/tasks/42/participations/team',
      args: [42, { agreeMentors: true }, 5],
      body: { agreeMentors: true, teamId: 5 },
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
      args: [42, 11, [{ text: '答案' }], 'impl'],
      query: [['stage', 'impl']],
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
    const { TaskBoardApi: next } = await import('./taskBoard')
    await contractRequest(test, () =>
      (next[name as keyof typeof next] as (...args: unknown[]) => Promise<unknown>)(...test.args)
    )
  })
})
