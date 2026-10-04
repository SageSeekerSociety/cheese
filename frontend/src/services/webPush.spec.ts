// 退出登录（`forget()`）和冷打开恢复会话（`restoreSession()`）都 await `disablePush()`。
// `navigator.serviceWorker.ready` 在「从没注册过 worker」的浏览器里永远不 settle
// （dev server 就是这样），等它的调用者会一直卡住——登出后本地会话清不掉，冷打开那层
// 「正在恢复登录状态」也永远收不起来。这一条钉住：没有 worker 时它照样按时返回。
import { afterEach, describe, expect, it, vi } from 'vitest'

describe('disablePush', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('没有 worker（ready 永不 settle）时按时返回，不把调用者挂住', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('PushManager', function PushManager() {})
    vi.stubGlobal('Notification', { permission: 'default' })
    vi.stubGlobal('navigator', {
      language: 'zh-CN',
      userAgent: 'vitest',
      serviceWorker: { ready: new Promise(() => {}) },
    })

    const { disablePush } = await import('./webPush')
    let done = false
    const running = disablePush().then(() => {
      done = true
    })

    // 还没到预算：确实还在等（证明它等的是 ready，不是立刻返回）。
    await vi.advanceTimersByTimeAsync(1_000)
    expect(done).toBe(false)

    // 过了预算：放弃等待，正常返回。
    await vi.advanceTimersByTimeAsync(5_000)
    expect(done).toBe(true)
    await running
  })
})
