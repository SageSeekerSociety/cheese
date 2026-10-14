// 存储层唯一入口：读写都不许把异常带给调用者，坏了的值按定好的两条判据处理。
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  storageAvailable,
  storageGet,
  storageGetJson,
  storageGetStrings,
  storageKeys,
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

describe('可用性探针', () => {
  it('能用就是 true', () => {
    expect(storageAvailable()).toBe(true)
    expect(storageAvailable('session')).toBe(true)
  })

  // 「读不出来」和「这台浏览器不给留东西」是两回事，探针答的是后一个。
  it('读得出来但写不进去（配额满）仍算能用', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })

    expect(storageAvailable()).toBe(true)
  })

  it('站点数据被禁时是 false，且不抛', () => {
    vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })

    expect(() => storageAvailable()).not.toThrow()
    expect(storageAvailable()).toBe(false)
  })

  it('读本身就抛时是 false', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })

    expect(storageAvailable()).toBe(false)
  })

  it('探一下不留下东西', () => {
    storageAvailable()

    expect(localStorage.length).toBe(0)
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

  // 调用者自己那层上限（草稿粘进一整个文件那种）：超了当作不写，写下去的还是原来那一份。
  it('超过 maxChars 的值不写，原来那一份不动', () => {
    storageSetJson('k', { n: '旧' })

    expect(storageSetJson('k', { n: '新'.repeat(20) }, 'local', 12)).toBe(false)
    expect(storageGetJson('k')).toEqual({ n: '旧' })

    expect(storageSetJson('k', { n: '新' }, 'local', 12)).toBe(true)
    expect(storageGetJson('k')).toEqual({ n: '新' })
  })
})

describe('按键前缀枚举', () => {
  it('只给前缀对得上的那些键', () => {
    storageSet('draft:a', '1')
    storageSet('draft:b', '2')
    storageSet('other', '3')
    storageSet('draft:c', '4', 'session')

    expect(storageKeys('draft:').sort()).toEqual(['draft:a', 'draft:b'])
  })

  it('一条都没有时是空数组', () => {
    expect(storageKeys('draft:')).toEqual([])
  })

  it('取不到存储对象时（存储被禁）是空数组', () => {
    vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('SecurityError')
    })

    expect(storageKeys('draft:')).toEqual([])
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
