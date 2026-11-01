// 市场（视图）：只吃 props。市场是找东西、装东西的地方——技能、插件、MCP、Agent 模板；
// 模型和环境是项目的配置，在项目设置里选（`ProjectComputeSettings`、默认模型）；题目在空间里。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import MarketViewView from './MarketViewView.vue'

import i18n, { setLocale, t } from '@/i18n'

const MARKET = {
  skills: [
    {
      name: 'pdf',
      description: '读 PDF',
      url: 'https://github.com/o/r/tree/main/skills/pdf',
      source: 'anthropic',
      kind: 'skill' as const,
    },
  ],
  plugins: [],
  plugin_total: 0,
  mcp: [],
  notes: [],
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

describe('MarketViewView', () => {
  it('opens on the skills and plugins catalog', async () => {
    render(MarketViewView, { props: { market: MARKET, marketLoaded: true }, global: { plugins: plugins() } })
    expect(screen.getByText(t('market.title'))).toBeTruthy()
    expect(await screen.findByText('pdf')).toBeTruthy()
  })

  // 只放能装的：模型和环境在项目设置里选，题目在空间里发布和应征。
  it('offers only installables: no models and environments, no challenge matching', () => {
    render(MarketViewView, { props: { market: MARKET, marketLoaded: true }, global: { plugins: plugins() } })
    expect(screen.queryByText('模型与环境')).toBeNull()
    expect(screen.queryByText('环境状态')).toBeNull()
    expect(screen.queryByText('题目匹配')).toBeNull()
    expect(document.body.textContent).not.toContain('机构把')
  })
})
