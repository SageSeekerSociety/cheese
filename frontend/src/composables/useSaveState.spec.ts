/** 一次保存的四态和两种回执方式（docs/design-system.md §3.11）。这里锁四件：
 *  保存中不重复发、inline 成功就地留痕再淡出、inline 失败把原因留下、
 *  toast 模式成功失败都弹条而不再需要 `error`。 */
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }))

import { useSaveState } from './useSaveState'

/** 在 effectScope 里跑，好让 onScopeDispose 有地方挂。 */
function inScope<T>(fn: () => T): { value: T; stop: () => void } {
  const scope = effectScope()
  const value = scope.run(fn) as T
  return { value, stop: () => scope.stop() }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  mocks.success.mockClear()
  mocks.error.mockClear()
})
afterEach(() => vi.useRealTimers())

describe('useSaveState', () => {
  it('inline 成功：saving → saved，回执自动淡出', async () => {
    vi.useFakeTimers()
    const { value: state, stop } = inScope(() => useSaveState({ feedback: 'inline', savedDuration: 1000 }))

    const action = deferred<string>()
    const run = state.run(() => action.promise)
    expect(state.saving.value).toBe(true)
    expect(state.saved.value).toBe(false)

    action.resolve('ok')
    await run
    expect(state.saving.value).toBe(false)
    expect(state.saved.value).toBe(true)
    expect(state.error.value).toBe('')

    vi.advanceTimersByTime(1000)
    await nextTick()
    expect(state.saved.value).toBe(false)
    expect(mocks.success).not.toHaveBeenCalled()
    stop()
  })

  it('inline 失败：错误原因留在 error，交给就地回执', async () => {
    const { value: state, stop } = inScope(() => useSaveState({ feedback: 'inline', messages: { failed: '保存失败' } }))

    const result = await state.run(() => Promise.reject(new Error('HTTP 500')))
    expect(result).toBeUndefined()
    expect(state.saving.value).toBe(false)
    expect(state.saved.value).toBe(false)
    expect(state.error.value).toBe('HTTP 500')
    // inline 不弹 toast：这块内容还在屏幕上，结果就留在旁边。
    expect(mocks.error).not.toHaveBeenCalled()
    stop()
  })

  it('失败没有原因时用兜底那句', async () => {
    const { value: state, stop } = inScope(() => useSaveState({ feedback: 'inline', messages: { failed: '保存失败' } }))
    await state.run(() => Promise.reject(new Error('')))
    expect(state.error.value).toBe('保存失败')
    stop()
  })

  it('toast 模式：成功弹成功条，失败弹失败条且不留 error', async () => {
    const { value: state, stop } = inScope(() =>
      useSaveState({ feedback: 'toast', messages: { saved: '已连接', failed: '连接失败' } })
    )

    await state.run(() => Promise.resolve(1))
    expect(mocks.success).toHaveBeenCalledWith('已连接')
    expect(state.error.value).toBe('')

    await state.run(() => Promise.reject(new Error('boom')))
    expect(mocks.error).toHaveBeenCalledWith('boom')
    expect(state.error.value).toBe('')
    stop()
  })

  it('保存中再点不重复发请求', async () => {
    const { value: state, stop } = inScope(() => useSaveState({ feedback: 'toast' }))
    const action = deferred<void>()
    const actionSpy = vi.fn(() => action.promise)

    const first = state.run(actionSpy)
    const second = state.run(actionSpy)
    await Promise.resolve()
    expect(actionSpy).toHaveBeenCalledTimes(1)
    expect(await second).toBeUndefined()

    action.resolve()
    await first
    stop()
  })

  it('dirty 跟着调用方给的判据走', async () => {
    const changed = ref(false)
    const { value: state, stop } = inScope(() => useSaveState({ dirty: () => changed.value }))
    expect(state.dirty.value).toBe(false)
    changed.value = true
    await nextTick()
    expect(state.dirty.value).toBe(true)
    stop()
  })
})
