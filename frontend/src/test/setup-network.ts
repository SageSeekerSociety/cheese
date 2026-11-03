import { afterEach, expect } from 'vitest'

import { installNetworkTrap, takeUnexpectedRequests } from './networkTrap'

import { queryClient } from '@/query/client'

// 服务器数据的缓存（query/client.ts）是模块级的：每个用例结束清空，上一个用例读到
// 的东西不会被下一个当成缓存先画出来。
afterEach(async () => {
  queryClient.clear()
  const { forgetRoomSnapshots } = await import('@/query/snapshot')
  forgetRoomSnapshots()
})

// 测试里任何一次真请求都当场失败，并指出是哪个用例（四条出口的细则见 networkTrap.ts）。
installNetworkTrap()

// Every room asks which authors off its roster closed their accounts
// (useClosedAuthors). A test that is not about that is told: none of them.
const trappedFetch = globalThis.fetch
globalThis.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  if (new URL(url, 'http://test').pathname.endsWith('/closed-accounts')) {
    return new Response(JSON.stringify({ code: 200, message: 'ok', data: { handles: [] } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  // Every composer asks who its draft is for (useAddressing). A test that is not
  // about that is told: nobody in particular, in a channel.
  if (new URL(url, 'http://test').pathname.endsWith('/messages/addressing')) {
    const data = { agent: null, reason: null, people: [], place: 'channel', task_status: null }
    return new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  return trappedFetch(input, init)
}

afterEach(() => {
  // 组件可能把请求的异常吞掉（一个 catch 就没了），那样当场抛出的那次不会让用例变红。
  // 所以陷阱还把每一次尝试记在这里，用例结束时再核对一次。
  const requests = takeUnexpectedRequests()
  expect(requests, 'Mock every network request used by this test').toEqual([])
})

// 房间连接（lib/roomLink.ts）也是模块级的：上一个用例开的那条（连着它那个替身
// WebSocket）不能让下一个用例的房间接着在上面订阅。
afterEach(async () => {
  const { resetRoomLink } = await import('@/lib/roomLink')
  resetRoomLink()
})
