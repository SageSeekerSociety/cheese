import { VueQueryPlugin } from '@tanstack/vue-query'
import { config } from '@vue/test-utils'
import { afterEach, expect } from 'vitest'

import { queryClient } from '@/lib/queryClient'

// 组件读服务器数据都经过同一个 QueryClient（lib/queryClient.ts）：每个挂载的组件都
// 装上它，每个用例结束清空，上一个用例读到的东西不会被下一个当成缓存先画出来。
config.global.plugins.push([VueQueryPlugin, { queryClient }])
afterEach(() => {
  queryClient.clear()
})

const unexpectedRequests: string[] = []

globalThis.fetch = async (input) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  unexpectedRequests.push(url)
  throw new Error(`Unexpected fetch in unit test: ${url}`)
}

afterEach(() => {
  // Components may catch fetch failures; unexpected requests must still fail the test.
  const requests = unexpectedRequests.splice(0)
  expect(requests, 'Mock every HTTP request used by this test').toEqual([])
})

// 房间连接（lib/roomLink.ts）也是模块级的：上一个用例开的那条（连着它那个替身
// WebSocket）不能让下一个用例的房间接着在上面订阅。
afterEach(async () => {
  const { resetRoomLink } = await import('@/lib/roomLink')
  resetRoomLink()
})
