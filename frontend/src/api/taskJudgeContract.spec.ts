// @vitest-environment jsdom
// 题目测试点的评测（`/tasks/…/judge…`、`/spaces/{id}/test-trials`）：逐个方法钉住它发出去的
// 浏览器请求（方法、地址与查询、JSON 正文），并核对载荷契约。
import { describe, it } from 'vitest'

import { type ContractCase, contractEnvironment, contractRequest } from './requestContract.spec.helper'

import { newTestSuite } from '@/lib/taskTestSuite'

contractEnvironment()

const suite = { ...newTestSuite(null), stage: undefined }

const cases: { label: string; name: string; test: ContractCase }[] = [
  {
    label: 'codeVersion',
    name: 'codeVersion',
    test: { method: 'GET', path: '/tasks/42/participants/11/code-version', args: [42, 11] },
  },
  {
    label: 'submissionJudge',
    name: 'submissionJudge',
    test: { method: 'GET', path: '/tasks/42/participants/11/submissions/88/judge', args: [42, 11, 88] },
  },
  {
    label: 'summaries',
    name: 'summaries',
    test: {
      method: 'GET',
      path: '/tasks/42/judge/summaries',
      args: [42, [88, 89]],
      query: [['submissionIds', '88,89']],
    },
  },
  {
    label: 'createTrial',
    name: 'createTrial',
    test: {
      method: 'POST',
      path: '/spaces/7/test-trials',
      args: [7, suite, [{ path: 'main.py', content: 'print(1)' }]],
      body: { testSuite: suite, files: [{ path: 'main.py', content: 'print(1)' }] },
    },
  },
  { label: 'trial', name: 'trial', test: { method: 'GET', path: '/tasks/test-trials/5', args: [5] } },
]

describe('题目测试点 browser contract', () => {
  it.each(cases)('$label preserves the request and returns the payload', async ({ name, test }) => {
    const { TaskJudgeApi: next } = await import('./taskJudge')
    await contractRequest(test, () =>
      (next[name as keyof typeof next] as (...args: unknown[]) => Promise<unknown>)(...test.args)
    )
  })
})
