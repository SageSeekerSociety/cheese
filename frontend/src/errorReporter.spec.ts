// 现场是人读的地方：进得去的必须是有人能修的东西。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const PAGE = '/projects/de808b13-ffd2-4b8a-9d1d-fba7babe389f/topics/6825f21d-8e95-42fe-91ed-e88f78a484a2'

let sent: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  // happy-dom 的 history.replaceState 不动 location，直接换掉它。
  vi.stubGlobal('location', { pathname: PAGE, search: '' })
  sent = vi.fn().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', sent)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('前端报错上报', () => {
  it('浏览器自己发的 ResizeObserver 通告不进现场', async () => {
    const { reportError } = await import('./errorReporter')
    reportError('ResizeObserver loop completed with undelivered notifications.')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).not.toHaveBeenCalled()
  })

  it('真的报错照常进现场', async () => {
    const { reportError } = await import('./errorReporter')
    reportError("Cannot read properties of undefined (reading 'id')", 'at TopicView.vue:12')
    await vi.advanceTimersByTimeAsync(20_000)

    expect(sent).toHaveBeenCalledTimes(1)
    const body = JSON.parse(String((sent.mock.calls[0][1] as RequestInit).body))
    expect(body.errors[0].message).toContain('Cannot read properties')
  })
})
