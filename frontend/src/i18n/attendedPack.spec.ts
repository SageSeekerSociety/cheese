// `loadLocale` 的 `unattended` 决定这一次取包算不算「没人在等」（见 index.ts），
// `unattendedPackIsLoading()` 把这个报给 `services/staleBuild.ts`，那边据此决定要不要拿整页
// 刷新去救一块下不来的代码。「没人在等」的那一边在 `unattendedPack.spec.ts` 里钉着（首屏那一次、
// 账号上那条语言跟过来的那一次），这一份钉另一边：有人在等的那一次（`chooseLocale`，人自己点
// 的）不能报成没人在等——报成了，他点的那一次取包失败会被当成「这一页比部署旧了」，一页一页
// 地刷。
//
// 造法同 `lazyCatalog.spec.ts`：en 的取包由一个闸门控制，用例放行才放行。这里的第一次取包得
// 是「有人在等」的那一次，所以先在 `vi.hoisted` 里把界面这一门钉在入口那一门（zh-CN）上：
// 入口那一份跟着入口来，模块求值时那一次取包当场就完、不经过闸门，en 也就还没被取过。setup
// 的预取（`src/test/setup-i18n.ts`）由下面那个替身挡住——它一取，这一份包就不在闸门上了。
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
  // 界面这一门先定在入口那一门：`resolveInitialLocale` 读这里，读到的就是它。
  localStorage.setItem('cheese:locale', 'zh-CN')
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
    // setup 的预取别动这一份包：它一取，闸门就空了（见上面）。
    async loadLocale() {},
  }
})

import i18n, { chooseLocale, localeReady, unattendedPackIsLoading } from './index'

describe('有人在等的那一次取包', () => {
  it('不算成没人在等', async () => {
    // 界面这一门是入口那一门：首屏那一次取包当场就完，也没人在等哪一份包。
    await localeReady
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(unattendedPackIsLoading()).toBe(false)

    const picking = chooseLocale('en')
    await vi.waitFor(() => expect(gate.held()).toBe(1))
    // 这一次有人在等（`chooseLocale` 等它到了才换过去）：`services/staleBuild.ts` 不能
    // 把它当成「服务器一直不给这一块」。
    expect(unattendedPackIsLoading()).toBe(false)

    gate.release()
    await picking
    expect(i18n.global.locale.value).toBe('en')
    expect(unattendedPackIsLoading()).toBe(false)
  })
})
