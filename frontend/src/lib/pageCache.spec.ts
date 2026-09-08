// 这层缓存要是错了，错法都是「看不见」的那种：多发一倍请求、内存悄悄长、或者
// 换了个人登录还看得到上一个人的项目。所以这里测的全是从外面能观察到的事实。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { clearPageCache, fetchCachedPage, hasCachedPage, readCachedPage, writeCachedPage } from './pageCache'

import AccountService from '@/services/account'

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('page cache', () => {
  beforeEach(() => clearPageCache())

  it('hands back what was last written under a key', () => {
    writeCachedPage('overview:p1', { name: 'One' })
    expect(hasCachedPage('overview:p1')).toBe(true)
    expect(readCachedPage('overview:p1')).toEqual({ name: 'One' })
    expect(hasCachedPage('overview:p2')).toBe(false)
    expect(readCachedPage('overview:p2')).toBeUndefined()
  })

  it('sends one request when two callers ask for the same key at once', async () => {
    const gate = deferred<string>()
    const fetcher = vi.fn(() => gate.promise)

    const first = fetchCachedPage('k', fetcher)
    const second = fetchCachedPage('k', fetcher)
    gate.resolve('value')

    expect(await first).toBe('value')
    expect(await second).toBe('value')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('sends a fresh request once the previous one has finished', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce('a').mockResolvedValueOnce('b')

    expect(await fetchCachedPage('k', fetcher)).toBe('a')
    expect(await fetchCachedPage('k', fetcher)).toBe('b')
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(readCachedPage('k')).toBe('b')
  })

  it('keeps two different keys apart', async () => {
    await fetchCachedPage('a', async () => 1)
    await fetchCachedPage('b', async () => 2)
    expect(readCachedPage('a')).toBe(1)
    expect(readCachedPage('b')).toBe(2)
  })

  it('remembers nothing about a request that failed', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('boom'))
    await expect(fetchCachedPage('k', fetcher)).rejects.toThrow('boom')
    expect(hasCachedPage('k')).toBe(false)

    // 失败没有留下一条「正在飞」的假账，下一次照常发得出去。
    expect(await fetchCachedPage('k', async () => 'ok')).toBe('ok')
  })

  it('stops growing: the oldest entries fall out once it is full', () => {
    for (let i = 0; i < 60; i += 1) writeCachedPage(`k${i}`, i)

    expect(hasCachedPage('k0')).toBe(false)
    expect(hasCachedPage('k9')).toBe(false)
    expect(hasCachedPage('k10')).toBe(true)
    expect(hasCachedPage('k59')).toBe(true)
    // 50 条上限：写进去 60 个 key，活下来的是最后 50 个。
    expect(Array.from({ length: 60 }, (_, i) => hasCachedPage(`k${i}`)).filter(Boolean)).toHaveLength(50)
  })

  it('keeps a key that is still being refreshed instead of ageing it out', () => {
    writeCachedPage('hot', 'v1')
    for (let i = 0; i < 40; i += 1) writeCachedPage(`k${i}`, i)
    writeCachedPage('hot', 'v2') // 又进了一次这个页面
    for (let i = 40; i < 60; i += 1) writeCachedPage(`k${i}`, i)

    expect(readCachedPage('hot')).toBe('v2')
  })

  it('is emptied by signing out, so the next person on this machine sees nothing', async () => {
    writeCachedPage('overview:p1', { name: 'Secret project' })
    await AccountService.logout()
    expect(hasCachedPage('overview:p1')).toBe(false)
    expect(readCachedPage('overview:p1')).toBeUndefined()
  })

  it('does not let a request that was in flight during sign-out repopulate the cache', async () => {
    const gate = deferred<string>()
    const pending = fetchCachedPage('overview:p1', () => gate.promise)

    await AccountService.logout()
    gate.resolve('data belonging to the previous account')
    await pending

    expect(hasCachedPage('overview:p1')).toBe(false)
  })
})
