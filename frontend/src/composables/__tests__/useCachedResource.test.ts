// 这个 composable 的全部意义是一句话：**进过一次的页面，再进去不再转圈**。
// 下面每个用例都只从外面看得见的四个量（data / loading / refreshing / error）
// 和「请求发了几次」来判断，不关心里面怎么实现的。
import type { App } from 'vue'

import { createApp, defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useCachedResource } from '../useCachedResource'

import { clearPageCache } from '@/lib/pageCache'
import AccountService from '@/services/account'

const apps: App[] = []

// 真的挂一个组件，而不是裸调 composable：onActivated / watch 都要有组件实例才
// 是它在页面里的样子。
function mount<T>(setup: () => T): T {
  let exposed!: T
  const app = createApp(
    defineComponent({
      setup() {
        exposed = setup()
        return () => h('div')
      },
    })
  )
  apps.push(app)
  app.mount(document.createElement('div'))
  return exposed
}

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

// 让所有已 resolve 的 promise 链跑完。
const settle = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => clearPageCache())
afterEach(() => {
  while (apps.length) apps.pop()?.unmount()
})

describe('第一次进一个页面', () => {
  it('没有任何缓存时转圈，取回来就画出来', async () => {
    const res = mount(() => useCachedResource('overview:p1', async () => ({ name: '项目一' })))

    expect(res.loading.value).toBe(true)
    expect(res.refreshing.value).toBe(false)
    expect(res.data.value).toBeUndefined()

    await settle()
    expect(res.loading.value).toBe(false)
    expect(res.data.value).toEqual({ name: '项目一' })
    expect(res.error.value).toBeNull()
  })

  it('取不回来时报错', async () => {
    const res = mount(() => useCachedResource('overview:p1', async () => Promise.reject(new Error('加载总览失败'))))

    await settle()
    expect(res.loading.value).toBe(false)
    expect(res.data.value).toBeUndefined()
    expect(res.error.value?.message).toBe('加载总览失败')
  })
})

describe('第二次进同一个页面', () => {
  it('绝不转圈：缓存内容第一帧就在，刷新在背后跑', async () => {
    const fetcher = vi.fn().mockResolvedValue({ name: '项目一' })
    mount(() => useCachedResource('overview:p1', fetcher))
    await settle()

    const again = mount(() => useCachedResource('overview:p1', fetcher))

    // 同步地、还没 await 任何东西的时候就已经有内容了。
    expect(again.loading.value).toBe(false)
    expect(again.data.value).toEqual({ name: '项目一' })
    expect(again.refreshing.value).toBe(true)

    await settle()
    expect(again.refreshing.value).toBe(false)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('背后那次刷新失败了也照常显示，不换成错误页', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ name: '项目一' }).mockRejectedValueOnce(new Error('网络抖了一下'))
    mount(() => useCachedResource('overview:p1', fetcher))
    await settle()

    const again = mount(() => useCachedResource('overview:p1', fetcher))
    await settle()

    expect(again.data.value).toEqual({ name: '项目一' })
    expect(again.error.value).toBeNull()
    expect(again.loading.value).toBe(false)
    expect(again.refreshing.value).toBe(false)
  })

  it('背后那次刷新带回了新内容，就地换掉', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ name: '旧名字' }).mockResolvedValueOnce({ name: '新名字' })
    mount(() => useCachedResource('overview:p1', fetcher))
    await settle()

    const again = mount(() => useCachedResource('overview:p1', fetcher))
    expect(again.data.value).toEqual({ name: '旧名字' })
    await settle()
    expect(again.data.value).toEqual({ name: '新名字' })
  })
})

describe('换了一个 key', () => {
  it('立刻显示新 key 的缓存，一点旧 key 的东西都不留', async () => {
    const key = ref('overview:p1')
    const fetcher = vi.fn(async (k: string) => ({ name: k }))
    const res = mount(() => useCachedResource(key, fetcher))
    await settle()

    // 让 p2 也先进过一次
    key.value = 'overview:p2'
    await nextTick()
    await settle()
    key.value = 'overview:p1'
    await nextTick()
    expect(res.data.value).toEqual({ name: 'overview:p1' })

    key.value = 'overview:p2'
    await nextTick()
    expect(res.data.value).toEqual({ name: 'overview:p2' })
    expect(res.loading.value).toBe(false)
  })

  it('新 key 没有缓存时立刻清空并转圈，不残留上一个项目的数据', async () => {
    const key = ref('overview:p1')
    const res = mount(() => useCachedResource(key, async (k) => ({ name: k })))
    await settle()
    expect(res.data.value).toEqual({ name: 'overview:p1' })

    key.value = 'overview:p2'
    await nextTick()
    expect(res.data.value).toBeUndefined()
    expect(res.loading.value).toBe(true)
  })

  it('旧 key 的响应晚回来也不许盖掉新 key 的内容', async () => {
    const slow = deferred<{ name: string }>()
    const key = ref('overview:p1')
    const res = mount(() =>
      useCachedResource(key, (k) => (k === 'overview:p1' ? slow.promise : Promise.resolve({ name: '项目二' })))
    )

    key.value = 'overview:p2'
    await nextTick()
    await settle()
    expect(res.data.value).toEqual({ name: '项目二' })

    slow.resolve({ name: '项目一（迟到的）' })
    await settle()

    expect(res.data.value).toEqual({ name: '项目二' })
    expect(res.loading.value).toBe(false)
    expect(res.refreshing.value).toBe(false)
  })

  it('旧 key 的请求失败了也不许把新 key 变成错误页', async () => {
    const slow = deferred<{ name: string }>()
    const key = ref('overview:p1')
    const res = mount(() =>
      useCachedResource(key, (k) => (k === 'overview:p1' ? slow.promise : Promise.resolve({ name: '项目二' })))
    )

    key.value = 'overview:p2'
    await nextTick()
    await settle()

    slow.reject(new Error('项目一挂了'))
    await settle()

    expect(res.error.value).toBeNull()
    expect(res.data.value).toEqual({ name: '项目二' })
  })
})

describe('同一个 key 的并发请求', () => {
  it('两个页面同时要同一份数据，只发一个请求', async () => {
    const gate = deferred<{ name: string }>()
    const fetcher = vi.fn(() => gate.promise)

    const a = mount(() => useCachedResource('overview:p1', fetcher))
    const b = mount(() => useCachedResource('overview:p1', fetcher))
    gate.resolve({ name: '项目一' })
    await settle()

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(a.data.value).toEqual({ name: '项目一' })
    expect(b.data.value).toEqual({ name: '项目一' })
  })

  it('回到页面时的刷新会并进正在飞的那一个，不会翻倍', async () => {
    const gate = deferred<{ name: string }>()
    const fetcher = vi.fn(() => gate.promise)
    const res = mount(() => useCachedResource('overview:p1', fetcher))

    void res.refresh()
    void res.refresh()
    gate.resolve({ name: '项目一' })
    await settle()

    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('回到一个保活着的页面', () => {
  it('重新取一次数，但不转圈——屏幕上一直是内容', async () => {
    const fetcher = vi.fn(async () => ({ name: '项目一' }))
    let seen: ReturnType<typeof useCachedResource<{ name: string }>> | null = null

    const Child = defineComponent({
      setup() {
        seen = useCachedResource('overview:p1', fetcher)
        return () => h('div')
      },
    })
    const show = ref(true)
    const app = createApp(
      defineComponent({ setup: () => () => h(KeepAlive, null, { default: () => (show.value ? h(Child) : null) }) })
    )
    apps.push(app)
    app.mount(document.createElement('div'))
    await settle()
    expect(fetcher).toHaveBeenCalledTimes(1)

    // 走开，再回来。保活之下组件没有重新挂载。
    show.value = false
    await nextTick()
    show.value = true
    await nextTick()

    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(seen!.loading.value).toBe(false)
    expect(seen!.data.value).toEqual({ name: '项目一' })
    expect(seen!.refreshing.value).toBe(true)
  })
})

describe('退出登录之后', () => {
  it('上一个人看过的页面要重新加载，不能直接画出来', async () => {
    const fetcher = vi.fn().mockResolvedValue({ name: '上一个人的项目' })
    mount(() => useCachedResource('overview:p1', fetcher))
    await settle()

    await AccountService.logout()

    const next = mount(() => useCachedResource('overview:p1', fetcher))
    expect(next.data.value).toBeUndefined()
    expect(next.loading.value).toBe(true)
  })
})
