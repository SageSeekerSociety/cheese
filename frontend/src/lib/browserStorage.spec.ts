// 存储层唯一入口：读写都不许把异常带给调用者，坏了的值按定好的两条判据处理。
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  storageGet,
  storageGetJson,
  storageGetStrings,
  storageRemove,
  storageSet,
  storageSetJson,
} from './browserStorage'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

describe('字符串读写', () => {
  it('两份存储各记各的', () => {
    expect(storageSet('k', 'local-value')).toBe(true)
    expect(storageSet('k', 'session-value', 'session')).toBe(true)

    expect(storageGet('k')).toBe('local-value')
    expect(storageGet('k', 'session')).toBe('session-value')
  })

  it('没存过就是 null', () => {
    expect(storageGet('missing')).toBeNull()
  })

  it('删掉之后就没了', () => {
    storageSet('k', 'v')
    storageRemove('k')

    expect(storageGet('k')).toBeNull()
  })

  // 隐私模式、配额写满：写是写不进去的，但调用者不能因此崩，也不能当写成功了。
  it('写不进去返回 false，不抛', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })

    expect(() => storageSet('k', 'v')).not.toThrow()
    expect(storageSet('k', 'v')).toBe(false)
  })

  it('读不出来返回 null，不抛', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })

    expect(() => storageGet('k')).not.toThrow()
    expect(storageGet('k')).toBeNull()
  })

  it('取不到存储对象时（存储被禁）读写都平安', () => {
    vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })

    expect(storageGet('k')).toBeNull()
    expect(storageSet('k', 'v')).toBe(false)
    expect(() => storageRemove('k')).not.toThrow()
  })
})

describe('JSON 读写', () => {
  it('存什么读回什么', () => {
    expect(storageSetJson('k', { a: [1, 'two'] })).toBe(true)

    expect(storageGetJson('k')).toEqual({ a: [1, 'two'] })
  })

  it('没存过就是 null', () => {
    expect(storageGetJson('missing')).toBeNull()
  })

  // 判据 1：解析不出来的记录不会再变得可解析，留着就是每次进来都在这里失败一次。
  it('解析不出来的记录读回 null，并且顺手删掉', () => {
    localStorage.setItem('k', '{ 这不是 JSON')

    expect(storageGetJson('k')).toBeNull()
    expect(localStorage.getItem('k')).toBeNull()
  })

  // 判据 2：能解析但形状不对的，读回来原样交给调用者，不许自己删。
  it('形状不对的记录不删', () => {
    localStorage.setItem('k', '{"unexpected":true}')

    expect(storageGetJson('k')).toEqual({ unexpected: true })
    expect(localStorage.getItem('k')).toBe('{"unexpected":true}')
  })

  it('序列化不了的值（循环引用）当作没写成功', () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular

    expect(storageSetJson('k', circular)).toBe(false)
    expect(localStorage.getItem('k')).toBeNull()
  })
})

describe('字符串数组', () => {
  it('读回存进去的那一份', () => {
    storageSetJson('k', ['a', 'b'])

    expect(storageGetStrings('k')).toEqual(['a', 'b'])
  })

  it('没存过是空数组', () => {
    expect(storageGetStrings('missing')).toEqual([])
  })

  it('不是数组、里面混了非字符串，都按过滤后的结果算', () => {
    storageSetJson('object', { a: 1 })
    storageSetJson('mixed', ['a', 1, null, 'b'])

    expect(storageGetStrings('object')).toEqual([])
    expect(storageGetStrings('mixed')).toEqual(['a', 'b'])
  })
})
