// 市场（视图）：只吃 props。两张页签能切；目录按「AI 模型 / 工作电脑」分组画卡片；
// 节点状态由页面递进来，NodeBoard 只画；目录读失败时那一块给一条重试的路，往外 emit。
import type { MarketNodes, MarketPools } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import MarketViewView from './MarketViewView.vue'

import i18n, { setLocale, t } from '@/i18n'

const pools: MarketPools = {
  ai: [
    {
      kind: 'ai',
      id: 'm1',
      label: 'Model A',
      tier: 'default',
      price: '$0',
      description: 'Default model',
      available: true,
      default: true,
    },
  ],
  compute: [
    {
      kind: 'compute',
      id: 'c1',
      label: 'Box',
      tier: 'byo',
      price: '$0',
      description: 'Your own machine',
      available: false,
      default: false,
    },
  ],
}

const nodes: MarketNodes = {
  nodes: [
    {
      id: 'n1',
      label: 'Node One',
      kind: 'cloud',
      online: true,
      current: true,
      detail: 'ok',
      description: 'A cloud node',
    },
  ],
  active_turns_total: 3,
  current_provider: 'cloud',
}

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

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

async function openPools() {
  await fireEvent.click(screen.getByText(t('market.tabs.pools')))
}

describe('MarketViewView', () => {
  it('renders the catalog and the node board from props alone', async () => {
    render(MarketViewView, { props: { pools, nodes }, global: { plugins: plugins() } })
    expect(screen.getByText(t('market.title'))).toBeTruthy()
    await openPools()
    expect(await screen.findByText('Model A')).toBeTruthy()
    expect(screen.getByText('Box')).toBeTruthy()
    expect(screen.getByText('Node One')).toBeTruthy()
    expect(screen.getByText(t('work.nodeBoard.active', { count: 3 }))).toBeTruthy()
  })

  it('asks the page to retry when the catalog failed to load', async () => {
    const view = render(MarketViewView, {
      props: { pools: null, error: 'boom' },
      global: { plugins: plugins() },
    })
    await openPools()
    await fireEvent.click(await screen.findByRole('button', { name: t('global.loadError.retry') }))
    expect(view.emitted('retry')).toHaveLength(1)
  })
})
