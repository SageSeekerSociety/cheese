// 节点状态的取数：挂上就读一次，之后每 15 秒刷一次；只有第一次读算 loading；卸载停表。
// 给了 enabled：它第一次为真才读、才起表，之后它再变假也不停。
import { defineComponent, h, nextTick, ref } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getMarketNodes = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return { ...actual, getMarketNodes: (...a: unknown[]) => getMarketNodes(...a) }
})

import { MARKET_NODES_REFRESH_MS, useMarketNodes } from './useMarketNodes'

const board = { nodes: [], active_turns_total: 0, current_provider: 'cloud' }

function host(options?: Parameters<typeof useMarketNodes>[0]) {
  let state!: ReturnType<typeof useMarketNodes>
  const view = render(
    defineComponent({
      setup() {
        state = useMarketNodes(options)
        return () => h('div')
      },
    })
  )
  return { view, state }
}

beforeEach(() => {
  vi.useFakeTimers()
  getMarketNodes.mockReset().mockResolvedValue(board)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useMarketNodes', () => {
  it('reads on mount, refreshes on a timer, and stops when unmounted', async () => {
    const { view, state } = host()
    expect(getMarketNodes).toHaveBeenCalledTimes(1)
    expect(state.loading.value).toBe(true)
    await vi.advanceTimersByTimeAsync(0)
    expect(state.board.value).toEqual(board)
    expect(state.loading.value).toBe(false)

    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(2)

    view.unmount()
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS * 2)
    expect(getMarketNodes).toHaveBeenCalledTimes(2)
  })

  it('keeps the reason when a read fails', async () => {
    getMarketNodes.mockRejectedValue(new Error('down'))
    const { state } = host()
    await vi.advanceTimersByTimeAsync(0)
    expect(state.error.value).toBe('down')
    expect(state.loading.value).toBe(false)
  })

  it('waits for enabled to turn on, then keeps polling after it turns off', async () => {
    const on = ref(false)
    const { view } = host({ enabled: () => on.value })
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS * 2)
    expect(getMarketNodes).not.toHaveBeenCalled()

    on.value = true
    await nextTick()
    expect(getMarketNodes).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(2)

    on.value = false
    await nextTick()
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(3)

    on.value = true
    await nextTick()
    expect(getMarketNodes).toHaveBeenCalledTimes(3)

    view.unmount()
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS * 2)
    expect(getMarketNodes).toHaveBeenCalledTimes(3)
  })
})
