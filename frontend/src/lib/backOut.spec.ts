import type { Router } from 'vue-router'

import { describe, expect, it, vi } from 'vitest'

import { closeOverlay, stepBack } from './backOut'

/** 只长 `backOut` 用到的那几根线：身后那一格的地址、落脚处解出来是什么、以及两个动作。 */
function fakeRouter(back: string | null, fullPath = '/resolved') {
  const push = vi.fn()
  const replace = vi.fn()
  const router = {
    options: back === null ? { history: { state: {} } } : { history: { state: { back } } },
    resolve: () => ({ fullPath }),
    back: vi.fn(),
    push,
    replace,
  }
  return { router: router as unknown as Router, back: router.back, push, replace }
}

describe('closeOverlay', () => {
  it('身后正是被盖住的那一页：退一格，真的把它弹掉', () => {
    const { router, back, replace } = fakeRouter('/projects/p1/library', '/projects/p1/library')
    closeOverlay(router, { name: 'home' })
    expect(back).toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it('身后是别的页（从别处跳进来的）：replace，不在身后留一条一模一样的地址', () => {
    const { router, back, replace } = fakeRouter('/somewhere/else', '/projects/p1/library')
    closeOverlay(router, { name: 'home' })
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith({ name: 'home' })
  })

  it('冷开、身后没有应用内来路：replace 到落脚处，不退出去', () => {
    const { router, back, replace } = fakeRouter(null)
    closeOverlay(router, '/feedback')
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith('/feedback')
  })

  it('落脚处这条路不认识：照样 replace，让路由自己报错，而不是把这一层留在屏幕上', () => {
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
    stepBack(router, '/feedback')
    expect(back).toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it('冷开：replace 到落脚处，不把整个应用退出去', () => {
    const { router, back, replace } = fakeRouter(null)
    stepBack(router, '/feedback')
    expect(back).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith('/feedback')
  })
})
