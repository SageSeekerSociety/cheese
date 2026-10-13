import { beforeEach, expect, it, vi } from 'vitest'

const asked = vi.hoisted(() => ({ paths: [] as string[] }))
vi.mock('./http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./http')>()),
  request: vi.fn(async (path: string) => {
    asked.paths.push(path)
    return { data: [], has_more: false, next: null, counts: { all: 0, mine: 0, helping: 0, others: 0 } }
  }),
}))

import { pageProjectTasks } from './tasks'

beforeEach(() => {
  asked.paths = []
})

it.each([
  [true, 'true'],
  [false, 'false'],
  [null, null],
  [undefined, null],
] as const)('请求保留采纳筛选 %s，并与分页、频道、负责人组合', async (accepted, expected) => {
  await pageProjectTasks('project', {
    status: 'closed',
    limit: 50,
    before: 'cursor',
    channel: 'front',
    whose: 'mine',
    accepted,
  })
  const query = new URLSearchParams(asked.paths[0].split('?')[1])
  expect(query.get('accepted')).toBe(expected)
  expect(query.get('status')).toBe('closed')
  expect(query.get('before')).toBe('cursor')
  expect(query.get('channel')).toBe('front')
  expect(query.get('whose')).toBe('mine')
})
