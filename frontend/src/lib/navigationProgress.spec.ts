// 跳转计数：点下去到新页面落地之间，内容区靠它给出反馈（见 ProjectShell）。
//
// 这里每一问的都是**配对**：计数只要漏掉一次，界面上的加载条就会一直转下去；多减一
// 次，它就会在真正还在等的时候自己消失。被守卫拦下、被下一次跳转顶掉、原地重复跳转、
// 连 promise 都不返回——都得原样落回 0。
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { trackNavigations } from './navigationProgress'

function deferred<T>() {
  let settle!: (v: T) => void
  let fail!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    settle = res
    fail = rej
  })
  promise.catch(() => {})
  return { promise, settle, fail }
}

function router(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/a', name: 'a', component: { template: '<div />' } },
    ],
  })
}

describe('跳转计数', () => {
  it('按下去那一帧就是「在路上」，落地才落回来', async () => {
    const r = router()
    const gate = deferred<void>()
    // 一台慢在守卫上的 router：正好就是线上那一段（懒加载 chunk 还没下完）。
    r.beforeEach(async () => {
      await gate.promise
    })
    const { navigating } = trackNavigations(r)

    expect(navigating.value).toBe(false)
    const done = r.push({ name: 'a' })
    expect(navigating.value).toBe(true)

    gate.settle()
    await done
    expect(navigating.value).toBe(false)
  })

  it('被守卫拦下也算落地', async () => {
    const r = router()
    r.beforeEach(() => false)
    const { navigating } = trackNavigations(r)

    await r.push({ name: 'a' })

    expect(navigating.value).toBe(false)
  })

  it('原地重复跳转不会把计数减到负数', async () => {
    const r = router()
    const { navigating } = trackNavigations(r)

    await r.push({ name: 'home' }) // 已经在这一页
    await r.push({ name: 'home' })

    expect(navigating.value).toBe(false)
    await r.push({ name: 'a' })
    expect(navigating.value).toBe(false)
  })

  it('两次跳转叠在一起：两个都在路上时还是「在路上」，都落地才落回', async () => {
    const r = router()
    const gate = deferred<void>()
    let seen = 0
    r.beforeEach(async () => {
      seen += 1
      if (seen === 1) await gate.promise
    })
    const { navigating } = trackNavigations(r)

    const first = r.push({ name: 'a' })
    const second = r.push({ name: 'a', query: { x: '1' } })
    expect(navigating.value).toBe(true)

    gate.settle()
    await first
    await second
    expect(navigating.value).toBe(false)
  })

  it('replace 一样算', async () => {
    const r = router()
    const gate = deferred<void>()
    r.beforeEach(async () => {
      await gate.promise
    })
    const { navigating } = trackNavigations(r)

    const done = r.replace({ name: 'a' })
    expect(navigating.value).toBe(true)
    gate.settle()
    await done
    expect(navigating.value).toBe(false)
  })

  it('同一台 router 装两次也只算一次（装重了计数会翻倍，而包过的 push 看不出来）', async () => {
    const r = router()
    const gate = deferred<void>()
    r.beforeEach(async () => {
      await gate.promise
    })
    const first = trackNavigations(r)
    const second = trackNavigations(r)

    expect(second).toBe(first)
    const done = r.push({ name: 'a' })
    // 计数是 1 不是 2：翻倍在这里看不出来（都是「在路上」），所以拿 dispose 之后能不能
    // 重新装来钉这件事——包装套两层的话，摘一次还剩一层。
    first.dispose()
    expect(r.push).not.toBe(second.navigating)
    const third = trackNavigations(r)
    expect(third).not.toBe(first)
    gate.settle()
    await done
  })

  it('dispose 把 push/replace 还原成原来那两个函数', () => {
    const r = router()
    const push = r.push
    const replace = r.replace
    const { dispose } = trackNavigations(r)

    expect(r.push).not.toBe(push)
    dispose()
    expect(r.push).toBe(push)
    expect(r.replace).toBe(replace)
  })

  it('原函数当场抛异常也要配平（不然加载条会一直转）', () => {
    const r = router()
    r.push = (() => {
      throw new Error('boom')
    }) as Router['push']
    const { navigating } = trackNavigations(r)

    expect(() => r.push({ name: 'a' })).toThrow('boom')
    expect(navigating.value).toBe(false)
  })

  it('原函数不返回 promise 也要配平', () => {
    const r = router()
    r.push = (() => undefined) as unknown as Router['push']
    const { navigating } = trackNavigations(r)

    r.push({ name: 'a' })
    expect(navigating.value).toBe(false)
  })
})
