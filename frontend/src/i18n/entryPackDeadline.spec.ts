// 挂载前等的不只是首屏那一门：入口那一门（zh-CN）还有一部分要另取
// （messages/zh-CN/deferred.ts），它也在挂载前等（`localeReady`）。这一取同样可能卡在
// 网络上——既不成功也不失败，可以一直不回话；没有期限时 `localeReady` 永远不落定，
// `main.ts` 那句「哪一样都不会让挂载失败，都有自己的期限」就不成立，人看到的是启动骨架
// 一直转。等过头就照 `catalogs` 里那份 catch 的打算来：缺的只是那几个命名空间，界面照常。
//
// 造法同 unattendedPack.spec.ts：这两份包在这一份文件里取不回来，setup 的预取换成空转
//（它先取一遍的话，就撞不上那份取不回来的包了；`loadPlatformSentences` 也是取 zh-CN
// 那份，同样让它别去取）。
import { describe, expect, it, vi } from 'vitest'

vi.mock('./messages/zh-CN', () => ({
  default: {},
  loadDeferred: () => new Promise(() => {}),
  loadSentences: () => Promise.resolve({}),
}))

vi.mock('./messages/en', () => ({ default: {} }))

vi.mock('./index', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./index')>()
  return { ...actual, async loadLocale() {}, async loadPlatformSentences() {} }
})

import { localeReady } from './index'

describe('the entry pack has a deadline too', () => {
  it('settles when the entry pack never comes back', async () => {
    // 包不回话，挂载的这一路也要走完：5 秒的期限一到就落定（见 index.ts 的
    // `waitForPackAtMost`）。
    await expect(localeReady).resolves.toBeUndefined()
  }, 10_000)
})
