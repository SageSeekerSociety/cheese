// @vitest-environment jsdom
// 题目测试点的评测（`/tasks/…/judge…`、`/spaces/{id}/test-trials`）：逐个方法钉住它发出去的
// 浏览器请求（方法、地址与查询、JSON 正文），并核对载荷契约。
import { describe, it } from 'vitest'

import { type ContractCase, contractEnvironment, contractRequest } from './requestContract.spec.helper'

import { newTestSuite, switchKind } from '@/lib/taskTestSuite'

contractEnvironment()

const suite = { ...newTestSuite(null), stage: undefined }
const commandSuite = {
  ...switchKind(newTestSuite(null), 'COMMAND'),
  stage: undefined,
  commands: ['python3 grade.py'],
  protectedFiles: [{ path: 'tests/check.py', content: 'assert True' }],
}

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
  {
    label: 'createTrial (command suite)',
    name: 'createTrial',
    test: {
      method: 'POST',
      path: '/spaces/7/test-trials',
      args: [7, commandSuite, []],
      body: { testSuite: commandSuite, files: [] },
    },
  },
  {
    label: 'selfTest',
    name: 'selfTest',
    test: {
      method: 'POST',
      path: '/tasks/42/participants/11/self-tests',
      args: [42, 11, 's2'],
      query: [['stage', 's2']],
    },
  },
  {
    label: 'selfTest without stage',
    name: 'selfTest',
    test: { method: 'POST', path: '/tasks/42/participants/11/self-tests', args: [42, 11, null] },
  },
  {
    label: 'latestSelfTest',
    name: 'latestSelfTest',
    test: {
      method: 'GET',
      path: '/tasks/42/participants/11/self-tests/latest',
      args: [42, 11, 's2'],
      query: [['stage', 's2']],
    },
  },
  {
    label: 'rejudge',
    name: 'rejudge',
    test: { method: 'POST', path: '/tasks/42/judge/rejudge', args: [42, 's1'], query: [['stage', 's1']] },
  },
  {
    label: 'rejudge every stage',
    name: 'rejudge',
    test: { method: 'POST', path: '/tasks/42/judge/rejudge', args: [42] },
  },
]

describe('题目测试点 browser contract', () => {
  it.each(cases)('$label preserves the request and returns the payload', async ({ name, test }) => {
    const { TaskJudgeApi: next } = await import('./taskJudge')
    await contractRequest(test, () =>
      (next[name as keyof typeof next] as (...args: unknown[]) => Promise<unknown>)(...test.args)
    )
  })
})
