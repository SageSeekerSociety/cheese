// 守卫自己的用例：四条出口各自被拦下，报错里带用例名和去处。
//
// 这里必须自己核对记录，所以每条用例结束时清一次 —— 不然守卫记下的「这次尝试」会在
// setup 的 afterEach 里再报一遍，把这条用例自己也弄红。
import { afterEach, describe, expect, it } from 'vitest'

import { takeUnexpectedRequests } from './networkTrap'

afterEach(() => {
  takeUnexpectedRequests()
})

describe('未 mock 的网络请求会当场失败并点名', () => {
  it('fetch', async () => {
    await expect(fetch('/api/topics')).rejects.toThrow(/Unexpected fetch in unit test: \/api\/topics/)
    // 报错里带用例名，才看得出是哪个用例漏了 mock。
    await expect(fetch('/api/topics')).rejects.toThrow(/未 mock 的网络请求会当场失败并点名/)
  })

  it('XMLHttpRequest', () => {
    const request = new XMLHttpRequest()
    request.open('GET', '/api/topics')
    expect(() => request.send()).toThrow(/Unexpected XMLHttpRequest in unit test: GET \/api\/topics/)
    // 报错里带用例名，才看得出是哪个用例漏了 mock。
    expect(() => request.send()).toThrow(/未 mock 的网络请求会当场失败并点名/)
  })

  it('XMLHttpRequest：构造和 open 本身不是请求，不报错', () => {
    expect(() => {
      const request = new XMLHttpRequest()
      request.open('GET', '/api/topics')
    }).not.toThrow()
  })

  it('WebSocket', () => {
    expect(() => new WebSocket('ws://localhost:3000/api/rooms/live')).toThrow(
      /Unexpected WebSocket in unit test: ws:\/\/localhost:3000\/api\/rooms\/live/
    )
  })

  it('navigator.sendBeacon', () => {
    expect(() => navigator.sendBeacon('/api/frontend-errors', 'x')).toThrow(
      /Unexpected navigator\.sendBeacon in unit test: \/api\/frontend-errors/
    )
  })

  it('像 msw 那样把全局 XHR 包一层的拦截器，接手的请求不会被误拦', () => {
    // msw 拦 XHR 的办法不是替换全局，而是把当时全局上的那个 XHR 包一层代理，在
    // construct 里接住实例、自己接上 send（@mswjs/interceptors 的
    // createXMLHttpRequestProxy + XMLHttpRequestController）。陷阱得容得下这一层，
    // 否则接口契约测试里连被 mock 的请求都发不出去。
    const seen: string[] = []
    const Wrapped = new Proxy(globalThis.XMLHttpRequest, {
      construct(target, args, newTarget) {
        const request = Reflect.construct(target, args, newTarget) as XMLHttpRequest
        request.send = () => {
          seen.push('handled')
        }
        return request
      },
    }) as unknown as typeof XMLHttpRequest

    const request = new Wrapped()
    request.open('GET', '/api/topics')
    expect(() => request.send()).not.toThrow()
    expect(seen).toEqual(['handled'])
    expect(takeUnexpectedRequests()).toEqual([])
  })

  it('四条出口的静态量还在，代码读得到', () => {
    expect(XMLHttpRequest.DONE).toBe(4)
    expect(WebSocket.OPEN).toBe(1)
  })
})
