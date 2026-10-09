// 「这份变了，再读一次」和同一份别处正要读的那一次怎么相处。
import { effectScope } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { expect, it, vi } from 'vitest'

import { queryClient, refreshQueries } from '@/query/client'

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
