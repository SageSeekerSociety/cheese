// 「任务命名」那一块的数据与动作：挂上读一次、点一下存一次，读口是 `enabled`。
import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getTaskNaming = vi.fn()
const setTaskNaming = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getTaskNaming: (...a: unknown[]) => getTaskNaming(...a),
    setTaskNaming: (...a: unknown[]) => setTaskNaming(...a),
  }
})

import { useProjectTaskNamingSettings } from './useProjectTaskNamingSettings'

const naming = (overrides: Record<string, unknown> = {}) => ({
  mode: 'auto',
  available: true,
  can_manage: true,
  ...overrides,
})

const tick = () => new Promise<void>((r) => setTimeout(r, 0))

function host(projectId: () => string = () => 'p', options?: Parameters<typeof useProjectTaskNamingSettings>[1]) {
  let state!: ReturnType<typeof useProjectTaskNamingSettings>
  const view = render(
    defineComponent({
      setup() {
        state = useProjectTaskNamingSettings(projectId, options)
        return () => h('div')
      },
    })
  )
  return { view, state }
}

beforeEach(() => {
  getTaskNaming.mockReset().mockResolvedValue(naming())
  setTaskNaming.mockReset().mockImplementation(async (_p: string, mode: string) => naming({ mode }))
})
afterEach(cleanup)

describe('useProjectTaskNamingSettings', () => {
  it('reads once on mount and folds the answer into state', async () => {
    const { state } = host()
    expect(getTaskNaming).toHaveBeenCalledTimes(1)
    expect(getTaskNaming).toHaveBeenCalledWith('p')
    await tick()
    expect(state.state.value).toEqual(naming())
  })

  it('saves a picked mode and folds the answer back in', async () => {
    const { state } = host()
    await tick()
    await state.choose('manual')
    expect(setTaskNaming).toHaveBeenCalledWith('p', 'manual')
    expect(state.state.value).toEqual(naming({ mode: 'manual' }))
  })

  it('does not save the mode that is already on', async () => {
    const { state } = host()
    await tick()
    await state.choose('auto')
    expect(setTaskNaming).not.toHaveBeenCalled()
  })

  it('does not save for someone who cannot manage the project', async () => {
    getTaskNaming.mockResolvedValue(naming({ can_manage: false }))
    const { state } = host()
    await tick()
    await state.choose('manual')
    expect(setTaskNaming).not.toHaveBeenCalled()
  })

  it('keeps the reason when a read fails', async () => {
    getTaskNaming.mockRejectedValue(new Error('down'))
    const { state } = host()
    await tick()
    expect(state.error.value).toBe('down')
  })

  it('flags busy while a save is on its way', async () => {
    let release!: (value: unknown) => void
    setTaskNaming.mockImplementation(() => new Promise((r) => (release = r)))
    const { state } = host()
    await tick()
    const pending = state.choose('manual')
    expect(state.busy.value).toBe(true)
    release(naming({ mode: 'manual' }))
    await pending
    expect(state.busy.value).toBe(false)
  })

  it('waits for enabled to turn on before reading', async () => {
    const on = ref(false)
    host(() => 'p', { enabled: () => on.value })
    await tick()
    expect(getTaskNaming).not.toHaveBeenCalled()

    on.value = true
    await nextTick()
    await tick()
    expect(getTaskNaming).toHaveBeenCalledTimes(1)
  })

  it('re-reads when the project changes', async () => {
    const id = ref('p')
    host(() => id.value)
    await tick()
    id.value = 'q'
    await nextTick()
    await tick()
    expect(getTaskNaming).toHaveBeenLastCalledWith('q')
  })
})
