/**
 * 模型详情抽屉：花费副系列与失败率行。
 *
 * 抽屉自己拉数据（收 `name`），所以 mock 的是 `@/api` 的 `getGatewayModel`。钉的地方：
 *
 * 1. **趋势图有花费副系列**（虚线）：数据孪生表里多一列「花费」。
 * 2. **失败率行**只在窗口里有请求时画。
 *
 * 模子照 `AdminModelsPage.spec.ts`：mock `@/api`、vue-i18n 键透传（带插值）、
 * `createVuetify`、stub `ResizeObserver` / `visualViewport`。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getGatewayModel = vi.fn()

vi.mock('@/api', () => ({
  getGatewayModel: (...a: unknown[]) => getGatewayModel(...a),
}))
// Keep the real module (the catalog in `@/i18n` is built with its `createI18n`);
// only the component's own `useI18n` is swapped for a key echo.
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminModelDetailDrawer from './AdminModelDetailDrawer.vue'

function detailPayload(over: Record<string, unknown> = {}) {
  return {
    model: {
      name: 'glm-5.2',
      label: 'GLM 5.2',
      origin: 'runtime',
      blocked: false,
      selectable: true,
      priced: true,
      offered: true,
      blocked_reasons: [],
      unpriced_reason: null,
      upstream: { model: 'anthropic/glm-5.2', host: 'open.bigmodel.cn', provider: 'anthropic' },
      prices: { input: 2.5e-6, output: 1e-5 },
      capabilities: {},
      usage: { spend_usd: 1, requests: 10, failed_requests: 1, total_tokens: 1000 },
    },
    series: [
      { date: '2026-09-22', spend_usd: 0.5, requests: 5, tokens: 500 },
      { date: '2026-09-23', spend_usd: 0.5, requests: 5, tokens: 500 },
    ],
    platform_usage: { calls: 10, tokens: 1000, cost_usd: 1, unpriced_tokens: 0, note: null },
    ...over,
  }
}

function mountDrawer(props: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { AdminModelDetailDrawer },
    template: '<v-app><AdminModelDetailDrawer v-bind="$attrs" /></v-app>',
    inheritAttrs: false,
  }
  return render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify] },
    props: { modelValue: true, name: 'glm-5.2', days: 7, ...props },
    attrs: props,
  })
}

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  getGatewayModel.mockReset().mockResolvedValue(detailPayload())
})

afterEach(cleanup)

describe('详情抽屉 · 趋势与失败率', () => {
  it('趋势图带花费副系列：数据孪生表里多一列', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.trend')
    // AdminLineChart 的 <details> 数据表按 series 逐列画：token 与花费各一列。
    expect(page.getAllByText('models.detail.series.tokens').length).toBeGreaterThan(0)
    expect(page.getAllByText('models.detail.series.spend').length).toBeGreaterThan(0)
  })

  it('失败率行：窗口失败数与占比（10 次 1 败 → 10%）', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.failedRequests')
    expect(page.getByText(/1 · 10%/)).toBeTruthy()
  })

  it('0 请求时不画失败率行（「没用到」不是「没失败」）', async () => {
    const payload = detailPayload()
    ;(payload.model as Record<string, unknown>).usage = {
      spend_usd: 0,
      requests: 0,
      failed_requests: 0,
      total_tokens: 0,
    }
    getGatewayModel.mockResolvedValue(payload)
    const page = mountDrawer()
    await page.findByText('models.detail.trend')
    expect(page.queryByText('models.detail.failedRequests')).toBeNull()
  })
})
