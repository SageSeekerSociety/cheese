// 对话栏读时间线只要房间里显示的那些：一个干着活的房间，块大多是队友干活的步骤，
// 不在服务端筛掉的话，一页里多半一行都画不出来，打开房间要一页页往回翻才凑满一屏。
import { beforeEach, expect, it, vi } from 'vitest'

const asked = vi.hoisted(() => ({ paths: [] as string[] }))
vi.mock('./http', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./http')>()),
  request: vi.fn(async (path: string) => {
    asked.paths.push(path)
    return { data: [], total: 0, has_more: false, oldest_id: null, has_newer: false, newest_id: null }
  }),
}))

import { listBlocks } from './blocks'

beforeEach(() => {
  asked.paths = []
})

it.each([
  ['最新一页', {}],
  ['往上翻', { before: 'b1' }],
  ['往下翻', { after: 'b1' }],
  ['在一条消息上打开', { around: 'b1' }],
])('%s只要房间里显示的', async (_label, cursor) => {
  await listBlocks('room-1', { limit: 50, ...cursor })

  const query = new URLSearchParams(asked.paths[0].split('?')[1])
  expect(query.get('shown')).toBe('true')
})
