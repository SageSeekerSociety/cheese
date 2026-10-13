// 项目框架（频道清单、未读、提醒档位）不再定时去问：别处变了，项目推一帧过来，变了的
// 那一份重读。
import type { Topic } from '@/cx_types'

import { effectScope, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listTopics: vi.fn(),
  getTopic: vi.fn(),
  getTopicUnread: vi.fn(),
  getPrivateUnread: vi.fn(),
  getTopicNotifyLevels: vi.fn(),
}))
vi.mock('@/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api')>()), ...api }))

interface FakeChannel {
  topic: string
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
  openRoomChannel: (topic: string) => {
    const channel: FakeChannel = {
      topic,
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
const { keys } = await import('@/query/keys')
const { notifyLevelsQuery, privateUnreadQuery, topicsQuery, unreadQuery } = await import('@/query/project')
const { useProjectFeed } = await import('@/query/projectFeed')

const ME = 'me'
const row = (id: string, at: string) => ({ id, title: id, last_activity_at: at }) as unknown as Topic

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 项目页那样看着这个项目：四样都在读，再订上项目推送。 */
async function watchProject(projectId = 'p') {
  const scope = effectScope()
  scope.run(() => {
    useQuery(topicsQuery(projectId), queryClient)
    useQuery(unreadQuery(projectId, ME), queryClient)
    useQuery(privateUnreadQuery(projectId, ME), queryClient)
    useQuery(notifyLevelsQuery(projectId), queryClient)
    useProjectFeed(ref(projectId), ME)
  })
  await flush()
  return { scope, channel: channels.at(-1)! }
}

function tell(channel: FakeChannel, frame: object) {
  channel.onmessage?.({ data: JSON.stringify({ type: 'state', ...frame }) })
}

afterEach(() => {
  channels.length = 0
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('项目推送', () => {
  function serve() {
    api.listTopics.mockResolvedValue({
      data: [row('a', '2026-10-09T10:00:00Z'), row('b', '2026-10-09T09:00:00Z')],
      total: 2,
    })
    api.getTopicUnread.mockResolvedValue({})
    api.getPrivateUnread.mockResolvedValue({})
    api.getTopicNotifyLevels.mockResolvedValue({})
  }

  it('订上项目时四样各读一次：订上之前的变化不会再推过来', async () => {
    serve()
    const { scope, channel } = await watchProject()
    expect(channel.topic).toBe('project:p')
    for (const fn of Object.values(api)) fn.mockClear()

    channel.onopen?.()
    await flush()

    expect(api.listTopics).toHaveBeenCalledTimes(1)
    expect(api.getTopicUnread).toHaveBeenCalledTimes(1)
    expect(api.getPrivateUnread).toHaveBeenCalledTimes(1)
    expect(api.getTopicNotifyLevels).toHaveBeenCalledTimes(1)
    scope.stop()
  })

  it('别的频道有人说话：只重读那一行，它挪到最上面；未读重读一次', async () => {
    serve()
    const { scope, channel } = await watchProject()
    channel.onopen?.()
    await flush()
    api.listTopics.mockClear()
    api.getTopicUnread.mockClear()
    api.getTopic.mockResolvedValue(row('b', '2026-10-09T11:00:00Z'))
    api.getTopicUnread.mockResolvedValue({ b: { count: 1, new: true, messages: 1 } })

    tell(channel, { resource: 'topics', id: 'b' })
    tell(channel, { resource: 'unread', id: 'b' })
    await flush()

    expect(api.listTopics).not.toHaveBeenCalled()
    const rows = queryClient.getQueryData<{ data: Topic[] }>(keys.projectTopics('p'))!.data
    expect(rows.map((topic) => topic.id)).toEqual(['b', 'a'])
    expect(queryClient.getQueryData(keys.projectUnread('p', ME))).toEqual({ b: { count: 1, new: true, messages: 1 } })
    scope.stop()
  })

  it('消息一条接一条地来：未读不跟着每一条问，几秒里的并成一次', async () => {
    serve()
    const { scope, channel } = await watchProject()
    vi.useFakeTimers()
    api.getTopicUnread.mockClear()

    for (let i = 0; i < 10; i += 1) tell(channel, { resource: 'unread', id: 'b' })
    await vi.advanceTimersByTimeAsync(2_500)

    expect(api.getTopicUnread).toHaveBeenCalledTimes(2)
    scope.stop()
    vi.useRealTimers()
  })

  it('不是这个项目的人：被拒之后不再重连', async () => {
    serve()
    const { scope, channel } = await watchProject()
    vi.useFakeTimers()

    channel.onmessage?.({ data: JSON.stringify({ type: 'error', code: 'forbidden' }) })
    channel.onclose?.()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(channels).toHaveLength(1)
    scope.stop()
    vi.useRealTimers()
  })

  it('连接断了：过一会儿重新订阅', async () => {
    serve()
    const { scope, channel } = await watchProject()
    vi.useFakeTimers()
    channel.onopen?.()

    channel.onclose?.()
    await vi.advanceTimersByTimeAsync(1_000)

    expect(channels).toHaveLength(2)
    expect(channels[1].topic).toBe('project:p')
    scope.stop()
    vi.useRealTimers()
  })
})
