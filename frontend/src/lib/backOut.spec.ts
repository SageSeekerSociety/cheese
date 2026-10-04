import type { RouteLocationRaw, Router } from 'vue-router'

import { describe, expect, it, vi } from 'vitest'

import { closeOverlay, stepBack } from './backOut'

/** `closeOverlay` 的落脚处，写成一条地址的形状 —— 它只看 `fullPath`。 */
function at(fullPath: string) {
  return { fullPath } as unknown as RouteLocationRaw
}

/** 只长 `backOut` 用到的那几根线。`resolve` **真的读参数**，所以「身后是不是要去的那
 *  一页」是算出来的，不是写死的；`resolve` 一被写死，这一组用例就全是空转。 */
function fakeRouter(back: string | null) {
  const push = vi.fn()
  const replace = vi.fn()
  const router = {
    options: { history: { state: back === null ? {} : { back } } },
    resolve: (to: RouteLocationRaw) => ({ fullPath: (to as { fullPath: string }).fullPath }),
    back: vi.fn(),
    push,
    replace,
  }
  return { router: router as unknown as Router, back: router.back, push, replace }
}

describe('closeOverlay', () => {
  it('身后正是要去的那一页：退一格，真的把它弹掉', () => {
    const { router, back, replace } = fakeRouter('/projects/p1/library')
    closeOverlay(router, at('/projects/p1/library'))
    expect(back).toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it('身后是别的页（从别处跳进来的）：replace，不在身后留一条几乎一样的地址', () => {
    const { router, back, replace } = fakeRouter('/somewhere/else')
    closeOverlay(router, at('/projects/p1/library'))
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith(at('/projects/p1/library'))
  })

  it('同一页、不同 query：不算「就是那一页」，replace 过去而不是错弹一格', () => {
    const { router, back, replace } = fakeRouter('/projects/p1/library?file=a.xlsx')
    closeOverlay(router, at('/projects/p1/library'))
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalled()
  })

  it('冷开、身后没有应用内来路：replace 到落脚处，不退出去', () => {
    const { router, back, replace } = fakeRouter(null)
    closeOverlay(router, at('/feedback'))
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith(at('/feedback'))
  })

  it('落脚处这条路不认识：照样 replace，而不是把这一层留在屏幕上', () => {
    const replace = vi.fn()
    const router = {
      options: { history: { state: { back: '/a' } } },
      resolve: () => {
        throw new Error('No match')
      },
      back: vi.fn(),
      replace,
    } as unknown as Router
    closeOverlay(router, { name: 'gone' })
    expect(replace).toHaveBeenCalledWith({ name: 'gone' })
  })
})

describe('stepBack', () => {
  it('身后有应用内来路：退一格', () => {
    const { router, back, replace } = fakeRouter('/feedback')
    stepBack(router, at('/feedback'))
    expect(back).toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it('冷开：replace 到落脚处，不把整个应用退出去', () => {
    const { router, back, replace } = fakeRouter(null)
    stepBack(router, at('/feedback'))
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith(at('/feedback'))
  })
})
