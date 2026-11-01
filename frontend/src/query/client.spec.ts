// 「这份变了，再读一次」和同一份别处正要读的那一次怎么相处。
import { effectScope } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { expect, it, vi } from 'vitest'

import { patchQuery, queryClient, refreshQueries } from '@/query/client'

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

// 频道推来「支线变了」：概览那一格标过期重读，对话栏同一刻也要这份去更新消息下面那一行。
// 两个都得拿到答案，不能一个把另一个作废掉。
it('a refresh does not cancel a read someone else starts right after it', async () => {
  let reads = 0
  const queryFn = vi.fn(async () => ++reads)
  queryClient.setQueryData(['threads', 'room'], 0)
  const scope = effectScope()
  scope.run(() => useQuery({ queryKey: ['threads', 'room'], queryFn, staleTime: 30_000 }, queryClient))
  await flush()
  queryFn.mockClear()

  const refreshed = refreshQueries({ queryKey: ['threads', 'room'] })
  const asked = queryClient.fetchQuery({ queryKey: ['threads', 'room'], queryFn, staleTime: 0 })
  await expect(asked).resolves.toBeGreaterThan(0)
  await refreshed
  expect(queryFn).toHaveBeenCalledTimes(1)
  scope.stop()
})

// 打开项目直接落在频道页时：清单还在路上，标已读那一步就要改这一份（`markRead` →
// `forgetReadLights` → `patchTopics`）。这时候手上没有数据，取消在途那次读等于把这份
// 永远留在「还没读过」——取消之后既没有东西写下去，也没有谁再发一次，侧栏就一直等在
// 那份清单上（`loadingTopics` 永远为真）。
it('patching a key nobody has read yet leaves the read in flight alone', async () => {
  const KEY = ['project', 'p', 'topics']
  let reads = 0
  // 这一次读什么时候落地由用例说了算：靠「先等一小会儿再改」的时序会随机器快慢翻转，
  // 读落到了前面，改动前后都会绿，这条用例就白写了。
  let land: () => void = () => {}
  const queryFn = vi.fn(() => {
    reads += 1
    return new Promise((resolve) => {
      land = () => resolve({ data: ['第 1 次读回来'] })
    })
  })
  const scope = effectScope()
  scope.run(() => useQuery({ queryKey: KEY, queryFn }, queryClient))
  await flush()
  expect(reads).toBe(1)
  expect(queryClient.getQueryData(KEY)).toBeUndefined()

  // 标已读顺手熄灯：缓存里还没有这份数据，这一改无事可做。
  await patchQuery(KEY, (rows: unknown) => rows)

  // 那次读照常在飞：它落地时东西就在手上了。
  land()
  await vi.waitFor(() => expect(queryClient.getQueryData(KEY)).toEqual({ data: ['第 1 次读回来'] }))
  expect(reads).toBe(1)
  scope.stop()
})
