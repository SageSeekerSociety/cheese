// 领题后代码仓库在后台放入起始代码：放好（或没放成）时，开着的项目总览不用手动刷新就换成
// 新的那一句。推送到了就重读；推送没到，隔一会儿自己再读。
import { effectScope, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getProject: vi.fn() }))
vi.mock('@/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api')>()), ...api }))

interface FakeChannel {
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
  drop: ReturnType<typeof vi.fn>
  readyState: number
}
const channels: FakeChannel[] = []
vi.mock('@/lib/roomLink', () => ({
  resetRoomLink: () => {},
  openRoomChannel: () => {
    const channel: FakeChannel = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      send: vi.fn(),
      close: vi.fn(),
      drop: vi.fn(),
      readyState: 1,
    }
    channels.push(channel)
    return channel
  },
}))

const { queryClient } = await import('@/query/client')
const { projectQuery } = await import('@/query/project')
const { useProjectFeed } = await import('@/query/projectFeed')

const row = (state: 'pending' | 'done') => ({
  id: 'p',
  name: '实验一',
  created_at: '2026-10-01T00:00:00Z',
  starter: { revision: 2, state, error: null },
})

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

afterEach(() => {
  channels.length = 0
  queryClient.clear()
  vi.useRealTimers()
  api.getProject.mockReset()
})

describe('项目那一行里起始代码的状态', () => {
  it('放好时推过来一帧，项目那一行马上重读', async () => {
    api.getProject.mockResolvedValueOnce(row('pending')).mockResolvedValue(row('done'))
    const scope = effectScope()
    const project = scope.run(() => {
      const query = useQuery(projectQuery('p'), queryClient)
      useProjectFeed(ref('p'), 'me')
      return query
    })!
    await flush()
    expect(project.data.value?.starter?.state).toBe('pending')

    channels.at(-1)!.onmessage?.({ data: JSON.stringify({ type: 'state', resource: 'project' }) })
    await flush()

    expect(project.data.value?.starter?.state).toBe('done')
    scope.stop()
  })

  it('推送没到：还在放的时候隔一会儿自己再读，放好了就不再读', async () => {
    vi.useFakeTimers()
    api.getProject.mockResolvedValueOnce(row('pending')).mockResolvedValue(row('done'))
    const scope = effectScope()
    const project = scope.run(() => useQuery(projectQuery('p'), queryClient))!
    await vi.advanceTimersByTimeAsync(0)
    expect(project.data.value?.starter?.state).toBe('pending')

    await vi.advanceTimersByTimeAsync(20_000)
    expect(project.data.value?.starter?.state).toBe('done')
    const reads = api.getProject.mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(api.getProject.mock.calls.length).toBe(reads)
    scope.stop()
  })
})
