// 「首帧之后」这个门：角标、装饰类的一批等它开了才发。两件事要成立——
//
// - 跳转落地那一拍门还没开：页面自己的请求就是在这个时候发出去的，装饰类排在它们后面
//   才有意义。
// - 还没开门就切走了，上一页那次不算数，按新页面重排。切得快的时候，人没停下来看过的
//   那一页压根不该发这一批。
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { armAfterFirstPaint, firstPaintDone } from './afterFirstPaint'

function router(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/a', name: 'a', component: { template: '<div />' } },
      { path: '/b', name: 'b', component: { template: '<div />' } },
    ],
  })
}

/** 让出一帧：`whenIdle` 在没有 `requestIdleCallback` 的环境里退回 `setTimeout(…, 0)`。 */
function nextFrame(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => {
  // 测试环境（happy-dom）没有 `requestIdleCallback`，走的是 `idle.ts` 里那条退路；
  // 写明这件事，免得哪天环境变了这里变成静默地什么都不跑。
  expect(window.requestIdleCallback).toBeUndefined()
})

describe('首帧之后', () => {
  it('跳转落地这一拍还没开门，让出一帧之后才开', async () => {
    const r = router()
    const stop = armAfterFirstPaint(r)
    try {
      await r.push('/a')
      expect(firstPaintDone.value).toBe(false)
      await nextFrame()
      expect(firstPaintDone.value).toBe(true)
    } finally {
      stop()
    }
  })

  it('门开之前切走：上一次作废，按新页面重排', async () => {
    const r = router()
    const stop = armAfterFirstPaint(r)
    try {
      await r.push('/a')
      await r.push('/b')
      expect(firstPaintDone.value).toBe(false)
      await nextFrame()
      expect(firstPaintDone.value).toBe(true)
    } finally {
      stop()
    }
  })

  it('开过之后不再翻回去：切页不会让它重排一次', async () => {
    const r = router()
    const stop = armAfterFirstPaint(r)
    try {
      await r.push('/a')
      await nextFrame()
      expect(firstPaintDone.value).toBe(true)
      await r.push('/b')
      expect(firstPaintDone.value).toBe(true)
    } finally {
      stop()
    }
  })
})
