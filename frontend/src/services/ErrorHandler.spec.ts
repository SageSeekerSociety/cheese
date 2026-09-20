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
    const denied = new Error('denied')
    denied.name = 'AccessDeniedError'
    await errorHandler.handle(denied)

    const locked = new Error('locked')
    locked.name = 'TeamLockedError'
    await errorHandler.handle(locked)

    expect(reportError).not.toHaveBeenCalled()
  })
})
