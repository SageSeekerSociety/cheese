// 市场（容器）：节点状态板在「模型与工作电脑」页签上。默认页签不读节点；第一次点开那个
// 页签才读、才起 15 秒的表；切回别的页签表照走（和板子在不设 eager 的 v-window-item 里一样）。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMarketNodes = vi.fn()
const getMarketPools = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getMarketNodes: (...a: unknown[]) => getMarketNodes(...a),
    getMarketPools: (...a: unknown[]) => getMarketPools(...a),
  }
})

import { MARKET_NODES_REFRESH_MS } from '@/composables/useMarketNodes'

import MarketView from './MarketView.vue'

import i18n, { setLocale, t } from '@/i18n'

const plugins = () => [createVuetify({ components, directives }), i18n]

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  getMarketPools.mockReset().mockResolvedValue({ ai: [], compute: [] })
  getMarketNodes.mockReset().mockResolvedValue({ nodes: [], active_turns_total: 0, current_provider: 'cloud' })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('MarketView', () => {
  it('polls nodes only once the pools tab has been opened, and keeps polling after leaving it', async () => {
    render(MarketView, { global: { plugins: plugins() } })
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS * 2)
    expect(getMarketPools).toHaveBeenCalledTimes(1)
    expect(getMarketNodes).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByText(t('market.tabs.pools')))
    await vi.advanceTimersByTimeAsync(0)
    expect(getMarketNodes).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(2)

    await fireEvent.click(screen.getByText(t('market.tabs.tasks')))
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(3)
  })
})
