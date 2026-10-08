import { afterEach, expect } from 'vitest'

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

// 话题面板缓存（lib/topicPanelCache.ts）是模块级的：同一个测试文件里上一个用例取到
// 的进度、名册、派出的活会在下一个用例里被「先画上次那份」画出来。每个用例结束擦掉。
// 动态 import 放在 afterEach 里：此时测试文件的 vi.mock 已经登记，拿到的是同一个模块
// 实例，也不会抢在 mock 之前把真的 api 加载进来。
afterEach(async () => {
  const { clearTopicPanelCache } = await import('@/lib/topicPanelCache')
  clearTopicPanelCache()
})

// 房间连接（lib/roomLink.ts）也是模块级的：上一个用例开的那条（连着它那个替身
// WebSocket）不能让下一个用例的房间接着在上面订阅。
afterEach(async () => {
  const { resetRoomLink } = await import('@/lib/roomLink')
  resetRoomLink()
})
