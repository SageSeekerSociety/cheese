// A failure a person was shown has to leave a trace.
//
// The global reporter hooks window.error, unhandledrejection and Vue's
// errorHandler — all three fire only when something CRASHED. A request that
// fails and is turned into a red toast crashes nothing, so none of them sees
// it, and until now the platform kept no record of it at all. That is the shape
// of most of what a user actually meets: 「操作失败」 and no way to find out why.
//
// What must NOT be reported is the other half: a refusal the product means. No
// permission, a locked team — those have a registered handler, they are the
// system working, and recording them would bury the failures that are not.
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/http'

const reportError = vi.fn()

vi.mock('@/errorReporter', () => ({
  reportError: (...args: unknown[]) => reportError(...args),
}))

vi.mock('vuetify-sonner', () => ({
  toast: { error: vi.fn(), warning: vi.fn() },
}))

const { default: errorHandler } = await import('./ErrorHandler')

describe('ErrorHandler', () => {
  beforeEach(() => {
    reportError.mockClear()
  })

  it('records a failure the user was shown but nothing else would catch', async () => {
    await errorHandler.handle(new Error('Request failed with status 500'))

    expect(reportError).toHaveBeenCalledTimes(1)
    const [message, , source] = reportError.mock.calls[0]
    expect(message).toBe('Request failed with status 500')
    expect(source).toBe('ui:Error')
  })

  it('carries the message the user actually saw when the error has none', async () => {
    await errorHandler.handle(new Error(''), { defaultMessage: '保存失败' })

    expect(reportError.mock.calls[0][0]).toBe('保存失败')
  })

  it('says which error class it was, so two different failures do not merge', async () => {
    const denied = new Error('nope')
    denied.name = 'SomeUnhandledApiError'
    await errorHandler.handle(denied)

    expect(reportError.mock.calls[0][2]).toBe('ui:SomeUnhandledApiError')
  })

  it('stays quiet for a refusal the product means', async () => {
    // 服务端给回绝起的名字就是 `ApiError` 的 `code`，注册过的那两条按名字对上。
    await errorHandler.handle(new ApiError(403, 'denied', 'AccessDeniedError'))
    await errorHandler.handle(new ApiError(403, 'locked', 'TeamLockedError'))

    expect(reportError).not.toHaveBeenCalled()
  })

  // 调用方自己在别处显示这次失败（弹窗里的一格、toast 的第二行）时用它：不要额外
  // 再弹一个 toast，但该记的那一笔照记；它只要服务端那一句，通用文案留在调用方。
  describe('refusal', () => {
    it('records the failure and hands back nothing when the server said nothing', async () => {
      const reason = await errorHandler.refusal(new Error('offline'), '保存失败')

      expect(reason).toBeNull()
      expect(reportError).toHaveBeenCalledTimes(1)
    })

    it('hands back what the server said', async () => {
      const reason = await errorHandler.refusal(
        new ApiError(409, '这个空间名称已被占用，换一个', 'ConflictError'),
        '创建失败，稍后重试'
      )

      expect(reason).toBe('这个空间名称已被占用，换一个')
    })

    it('still says nothing for a refusal the product means', async () => {
      await errorHandler.refusal(new ApiError(403, 'denied', 'AccessDeniedError'), '兜底')

      expect(reportError).not.toHaveBeenCalled()
    })
  })
})
