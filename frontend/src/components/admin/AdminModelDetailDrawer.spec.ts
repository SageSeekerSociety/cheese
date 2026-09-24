/**
 * 模型详情抽屉：订阅块（额度条、刷新读数、重新授权、移除）与花费副系列。
 *
 * 抽屉自己拉数据（收 `name`），所以 mock 的是 `@/api` 那几个函数。钉的地方：
 *
 * 1. **额度条按 tier 画**：两个 tier 两条，各带名字、百分比、重置时间 —— 窗口
 *    挤在一条里，人分不清哪个先用完。
 * 2. **「刷新读数」真的发 quota 请求**，回来的 stale 标记要显示出来（「没刷出
 *    新读数」和「读数很新」是两句不同的话）。
 * 3. **reauth_required 给「重新授权」按钮**：凭据被判死时，这个抽屉是恢复它的
 *    地方；active 时不该摆着一个会让人换号的按钮。
 * 4. **移除先确认**，确认后才发请求 —— 它连带停用网关模型。
 * 5. **趋势图有花费副系列**（虚线）：数据孪生表里多一列「花费」。
 *
 * 模子照 `AdminModelsPage.spec.ts`：mock `@/api`、vue-i18n 键透传（带插值）、
 * `createVuetify`、stub `ResizeObserver` / `visualViewport`。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getGatewayModel = vi.fn()
const getSubscriptionQuota = vi.fn()
const revokeSubscription = vi.fn()
const updateSubscriptionUpstreamModel = vi.fn()
const startSubscriptionDeviceFlow = vi.fn()
const pollSubscriptionDeviceFlow = vi.fn()
const cancelSubscriptionDeviceFlow = vi.fn()

vi.mock('@/api', () => ({
  getGatewayModel: (...a: unknown[]) => getGatewayModel(...a),
  getSubscriptionQuota: (...a: unknown[]) => getSubscriptionQuota(...a),
  revokeSubscription: (...a: unknown[]) => revokeSubscription(...a),
  updateSubscriptionUpstreamModel: (...a: unknown[]) => updateSubscriptionUpstreamModel(...a),
  startSubscriptionDeviceFlow: (...a: unknown[]) => startSubscriptionDeviceFlow(...a),
  pollSubscriptionDeviceFlow: (...a: unknown[]) => pollSubscriptionDeviceFlow(...a),
  cancelSubscriptionDeviceFlow: (...a: unknown[]) => cancelSubscriptionDeviceFlow(...a),
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    locale: { value: 'zh-CN' },
  }),
}))

import AdminModelDetailDrawer from './AdminModelDetailDrawer.vue'

function detailPayload(over: Record<string, unknown> = {}) {
  return {
    model: {
      name: 'gpt-codex-subscription',
      label: 'GPT · ChatGPT 订阅',
      origin: 'runtime',
      blocked: false,
      selectable: true,
      priced: true,
      offered: true,
      blocked_reason: null,
      unpriced_reason: null,
      upstream: { model: 'openai/gpt-5.2-codex', host: 'chatgpt.com', provider: 'openai' },
      prices: { input: 2.5e-6, output: 1e-5 },
      capabilities: {},
      usage: { spend_usd: 1, requests: 10, failed_requests: 1, total_tokens: 1000 },
      subscription: {
        id: 'sub-1',
        status: 'active',
        account_email: 'admin@example.com',
        token_expires_at: '2026-09-23T12:00:00+00:00',
        last_refresh_error: null,
        quota: {
          tiers: [
            { name: 'five_hour', utilization: 42, resets_at: '2026-09-23T08:00:00+00:00' },
            { name: 'seven_day', utilization: 7, resets_at: '2026-09-30T00:00:00+00:00' },
          ],
          fetched_at: '2026-09-23T02:40:00+00:00',
        },
      },
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
    props: { modelValue: true, name: 'gpt-codex-subscription', days: 7, ...props },
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
  getSubscriptionQuota.mockReset().mockResolvedValue({
    tiers: [{ name: 'five_hour', utilization: 50, resets_at: '2026-09-23T09:00:00+00:00' }],
    queried_at: '2026-09-23T03:00:00+00:00',
    stale: false,
  })
  revokeSubscription.mockReset().mockResolvedValue({ revoked: true })
  updateSubscriptionUpstreamModel.mockReset().mockResolvedValue({})
  startSubscriptionDeviceFlow.mockReset()
  pollSubscriptionDeviceFlow.mockReset()
  cancelSubscriptionDeviceFlow.mockReset()
})

afterEach(cleanup)

describe('详情抽屉 · 订阅块', () => {
  it('额度条按 tier 画：两个窗口两条，各带名字与百分比', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')

    expect(page.getByText('models.detail.subscription.tier.fiveHour')).toBeTruthy()
    expect(page.getByText('models.detail.subscription.tier.sevenDay')).toBeTruthy()
    expect(page.getByText('42%')).toBeTruthy()
    expect(page.getByText('7%')).toBeTruthy()
    // 账号与状态也在（同列表语义的三态灯 + 文字）。
    expect(page.getByText(/admin@example\.com/)).toBeTruthy()
    expect(page.getByText('models.table.subscriptionOk')).toBeTruthy()
  })

  it('没有订阅的模型不画订阅块', async () => {
    const payload = detailPayload()
    ;(payload.model as Record<string, unknown>).subscription = null
    getGatewayModel.mockResolvedValue(payload)
    const page = mountDrawer()
    await page.findByText('models.detail.trend')
    expect(page.queryByText('models.detail.subscription.title')).toBeNull()
  })

  it('「刷新读数」真的发 quota 请求，新读数换上去', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')

    await fireEvent.click(page.getByRole('button', { name: 'models.detail.subscription.quotaRefresh' }))
    await waitFor(() => expect(getSubscriptionQuota).toHaveBeenCalledWith('sub-1'))
    // 新读数（50%）换上，旧的 42% 退场。
    expect(await page.findByText('50%')).toBeTruthy()
    expect(page.queryByText('42%')).toBeNull()
  })

  it('stale 回包标出「还是旧读数」', async () => {
    getSubscriptionQuota.mockResolvedValue({
      tiers: [{ name: 'five_hour', utilization: 42, resets_at: '2026-09-23T08:00:00+00:00' }],
      queried_at: '2026-09-23T02:40:00+00:00',
      stale: true,
    })
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')
    await fireEvent.click(page.getByRole('button', { name: 'models.detail.subscription.quotaRefresh' }))
    expect(await page.findByText(/quotaStale/)).toBeTruthy()
  })

  it('刷新读数失败：原话就地显示，旧读数不丢', async () => {
    getSubscriptionQuota.mockRejectedValue(new Error('凭据已失效，需要重新授权'))
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')

    await fireEvent.click(page.getByRole('button', { name: 'models.detail.subscription.quotaRefresh' }))
    expect(await page.findByText(/凭据已失效/)).toBeTruthy()
    expect(page.getByText('42%')).toBeTruthy()
  })

  it('reauth_required 显示「重新授权」；active 不显示', async () => {
    const payload = detailPayload()
    ;(payload.model.subscription as Record<string, unknown>).status = 'reauth_required'
    getGatewayModel.mockResolvedValue(payload)
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')
    expect(page.getByRole('button', { name: 'models.detail.subscription.reauth' })).toBeTruthy()
    expect(page.getByText('models.table.subscriptionReauth')).toBeTruthy()

    cleanup()
    getGatewayModel.mockResolvedValue(detailPayload())
    const active = mountDrawer()
    await active.findByText('models.detail.subscription.title')
    expect(active.queryByRole('button', { name: 'models.detail.subscription.reauth' })).toBeNull()
  })

  it('上游模型：显示订阅的显式选择；编辑保存发 PATCH 并带上 trim 后的值', async () => {
    const payload = detailPayload()
    ;(payload.model.subscription as Record<string, unknown>).upstream_model = 'openai/gpt-5.6-luna'
    getGatewayModel.mockResolvedValue(payload)
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')
    // 显式选择：显示行上的值，不是网关现值。
    expect(page.getByText(/upstreamModelCurrent.*gpt-5.6-luna/)).toBeTruthy()

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelEdit' }))
    const input = page.getByTestId('upstream-edit-input').querySelector('input') as HTMLInputElement
    expect(input.value).toBe('openai/gpt-5.6-luna')
    await fireEvent.update(input, '  openai/gpt-5.6-sol  ')
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelSave' }))
    await waitFor(() => expect(updateSubscriptionUpstreamModel).toHaveBeenCalledWith('sub-1', 'openai/gpt-5.6-sol'))
  })

  it('上游模型：没有显式选择时显示「默认（网关现值）」；清空后保存发 null', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')
    expect(page.getByText(/upstreamModelDefault.*gpt-5.2-codex/)).toBeTruthy()

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelEdit' }))
    const input = page.getByTestId('upstream-edit-input').querySelector('input') as HTMLInputElement
    expect(input.value).toBe('')
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelSave' }))
    await waitFor(() => expect(updateSubscriptionUpstreamModel).toHaveBeenCalledWith('sub-1', null))
  })

  it('上游模型保存失败：服务端原话就地显示，编辑框不关', async () => {
    updateSubscriptionUpstreamModel.mockRejectedValue(new Error('网关 400：bad upstream'))
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelEdit' }))
    const input = page.getByTestId('upstream-edit-input').querySelector('input') as HTMLInputElement
    await fireEvent.update(input, 'openai/bad')
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.upstreamModelSave' }))
    expect(await page.findByText(/bad upstream/)).toBeTruthy()
    expect(page.getByTestId('upstream-edit-input')).toBeTruthy()
  })

  it('移除订阅先确认，确认后才发请求', async () => {
    const page = mountDrawer()
    await page.findByText('models.detail.subscription.title')

    await fireEvent.click(page.getByRole('button', { name: 'models.detail.subscription.revoke' }))
    expect(revokeSubscription).not.toHaveBeenCalled()
    await page.findByText(/revokeBody/)

    // 确认框里那颗也叫 revoke：按文本精确命中两颗，取对话框里的确认（最后一颗）。
    const buttons = page.getAllByRole('button', { name: 'models.detail.subscription.revoke' })
    await fireEvent.click(buttons[buttons.length - 1])
    await waitFor(() => expect(revokeSubscription).toHaveBeenCalledWith('sub-1'))
  })
})

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
