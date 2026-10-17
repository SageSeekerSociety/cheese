// 没人在等的语言包和别的代码块不一样：开机那一次每次打开都要取，账号上那条语言跟过来的
// 那一次在任何一次导航之外（`services/account.ts`），两者取不回来时也各有自己的落点，所以
// `services/staleBuild.ts` 的全局监听看不出它和「这一页比部署旧了」的区别——服务器一直不给
// 这一块，它就会一轮一轮地整页刷新。哪几次取算「没人在等」由 `loadLocale` 的 `unattended`
// 说，取包的这段时间由 `unattendedPackIsLoading()` 报给那边（见 index.ts），取完（无论成败）
// 恢复。
//
// 造法同 lazyCatalog.spec.ts：这份文件里 en 的取包由一个闸门控制，用例放行才放行。这里要
// 看的是模块求值时开始的那一次取包（`localeReady` 那次），所以 setup 的预取
// （`src/test/setup-i18n.ts`）换成空转——它先取一遍的话，这一份包就不在闸门上了。
import { describe, expect, it, vi } from 'vitest'

const gate = vi.hoisted(() => {
  const held: Array<() => void> = []
  const gate = {
    held: () => held.length,
    wait<T>(value: T): Promise<T> {
      return new Promise<T>((resolve) => held.push(() => resolve(value)))
    },
    release() {
      for (const settle of held.splice(0)) settle()
    },
  }
  return gate
})

vi.mock('./messages/en', async (importOriginal) => {
  const actual = await importOriginal<{ default: unknown }>()
  return {
    get default() {
      return gate.wait(actual.default)
    },
  }
})

vi.mock('./index', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./index')>()
  return {
    ...actual,
    // setup 的预取别动这一份包：它一取，闸门就空了。
    async loadLocale() {},
  }
})

import { localeReady, unattendedPackIsLoading } from './index'

describe('没人在等的语言包还在路上', () => {
  it('这段时间里报 true，落定之后恢复', async () => {
    await vi.waitFor(() => expect(gate.held()).toBe(1), { timeout: 10_000 })
    expect(unattendedPackIsLoading()).toBe(true)

    gate.release()
    await localeReady
    expect(unattendedPackIsLoading()).toBe(false)
  })
})
