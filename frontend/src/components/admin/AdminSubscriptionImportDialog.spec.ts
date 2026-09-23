/**
 * 「导入订阅」对话框：device-code UX 的状态机。
 *
 * 钉的是那些「界面看着没事、实际已经坏了」的地方：
 *
 * 1. **轮询三态各进各的态**。pending 继续等（且真的在按间隔发 poll）、complete
 *    进 success、expired 进「重新开始」。轮询用 `vi.useFakeTimers()` 控 —— 真等
 *    3 秒钟的测试既慢又脆。
 * 2. **取消是真的发 cancel**。点「取消导入」必须打到服务端那条 cancel，不然一条
 *    pending 行要在库里挂 15 分钟等自然过期。
 * 3. **error 显示服务端原话，且框不关**。改写一句「授权失败」就是把原因丢了；
 *    关掉框等于把刚填的备注名和原因一起丢。
 * 4. **关闭停止轮询**。框关了，定时器必须清 —— 服务端的 flow 会自己过期，页面
 *    不留一个空转的轮询。
 *
 * 模子照 `AdminModelsPage.spec.ts`：mock `@/api`、vue-i18n 键透传、`createVuetify`、
 * stub `ResizeObserver` / `visualViewport`（对话框浮层定位要读后者）。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const startSubscriptionDeviceFlow = vi.fn()
const pollSubscriptionDeviceFlow = vi.fn()
const cancelSubscriptionDeviceFlow = vi.fn()

vi.mock('@/api', () => ({
  startSubscriptionDeviceFlow: (...a: unknown[]) => startSubscriptionDeviceFlow(...a),
  pollSubscriptionDeviceFlow: (...a: unknown[]) => pollSubscriptionDeviceFlow(...a),
  cancelSubscriptionDeviceFlow: (...a: unknown[]) => cancelSubscriptionDeviceFlow(...a),
}))
vi.mock('vue-i18n', () => ({
  // 键透传，但带上插值参数 —— 不然断言「账号 email 显示出来」时拿不到值。
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
  }),
}))

import AdminSubscriptionImportDialog from './AdminSubscriptionImportDialog.vue'

const FLOW = {
  flow_id: 'flow-1',
  user_code: 'ABCD-EFGH',
  verification_uri: 'https://auth.openai.com/codex/device',
  expires_in: 900,
  interval: 5,
}

function mountDialog(props: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { AdminSubscriptionImportDialog },
    template: '<v-app><AdminSubscriptionImportDialog v-bind="$attrs" /></v-app>',
    inheritAttrs: false,
  }
  return render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify] },
    props: { modelValue: true, ...props },
    attrs: props,
  })
}

/** 打开对话框并走到 waiting 态（start 已答、flow 在手）。 */
async function toWaiting(page: ReturnType<typeof mountDialog>) {
  await fireEvent.click(page.getByRole('button', { name: 'models.subscription.start' }))
  await page.findByText('ABCD-EFGH')
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
  vi.useFakeTimers()
  startSubscriptionDeviceFlow.mockReset().mockResolvedValue(FLOW)
  pollSubscriptionDeviceFlow.mockReset().mockResolvedValue({ state: 'pending' })
  cancelSubscriptionDeviceFlow.mockReset().mockResolvedValue({ cancelled: true })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('导入订阅 · 状态机', () => {
  it('开始授权后进入 waiting：代码大字、复制、授权页链接、倒计时都在', async () => {
    const page = mountDialog()
    await page.findByText('models.subscription.intro')

    await toWaiting(page)
    expect(startSubscriptionDeviceFlow).toHaveBeenCalledWith({
      provider: 'openai_codex',
      label: null,
      target_subscription_id: null,
    })
    expect(page.getByText('models.subscription.codeHint')).toBeTruthy()
    const link = page.getByRole('link', { name: 'models.subscription.openPage' }) as HTMLAnchorElement
    expect(link.href).toBe('https://auth.openai.com/codex/device')
    expect(link.target).toBe('_blank')
    expect(page.getByText(/expiresIn/)).toBeTruthy()
  })

  it('pending 继续等：按 interval 真发轮询；complete 进 success 并停止', async () => {
    const page = mountDialog()
    await toWaiting(page)
    expect(pollSubscriptionDeviceFlow).not.toHaveBeenCalled()

    // interval=5s：走两个周期，真的发了两次 poll。
    await vi.advanceTimersByTimeAsync(5000)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledTimes(2)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledWith('flow-1')

    pollSubscriptionDeviceFlow.mockResolvedValue({
      state: 'complete',
      subscription: { id: 'sub-1', status: 'active', account_email: 'admin@example.com' },
    })
    await vi.advanceTimersByTimeAsync(5000)
    expect(await page.findByText('models.subscription.success')).toBeTruthy()
    expect(page.getByText(/admin@example\.com/)).toBeTruthy()
    // 到了 success 就不再轮询。
    await vi.advanceTimersByTimeAsync(20000)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledTimes(3)
  })

  it('expired 进「重新开始」，点了回到 start 态', async () => {
    pollSubscriptionDeviceFlow.mockResolvedValue({ state: 'expired' })
    const page = mountDialog()
    await toWaiting(page)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await page.findByText('models.subscription.expired')).toBeTruthy()
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.restart' }))
    expect(await page.findByText('models.subscription.intro')).toBeTruthy()
  })

  it('本地倒计时到 0 也进 expired（不等服务端那一枪）', async () => {
    const page = mountDialog()
    await toWaiting(page)
    await vi.advanceTimersByTimeAsync(900 * 1000)
    expect(await page.findByText('models.subscription.expired')).toBeTruthy()
  })

  it('定向重授权：start 的体里带上 target，文案换成「重新授权」那句', async () => {
    const page = mountDialog({ targetSubscriptionId: 'sub-old' })
    await page.findByText('models.subscription.reauthIntro')

    await toWaiting(page)
    expect(startSubscriptionDeviceFlow).toHaveBeenCalledWith({
      provider: 'openai_codex',
      label: null,
      target_subscription_id: 'sub-old',
    })
  })
})

describe('导入订阅 · 取消与关闭', () => {
  it('「取消导入」真的发 cancel 请求，并停止轮询', async () => {
    const page = mountDialog()
    await toWaiting(page)

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.cancelImport' }))
    await waitFor(() => expect(cancelSubscriptionDeviceFlow).toHaveBeenCalledWith('flow-1'))
    await vi.advanceTimersByTimeAsync(20000)
    expect(pollSubscriptionDeviceFlow).not.toHaveBeenCalled()
  })

  it('关闭对话框后不再轮询（服务端的 flow 自己过期，页面不留空转的定时器）', async () => {
    const page = mountDialog({
      'onUpdate:modelValue': () => {},
    })
    await toWaiting(page)

    // 直接换 prop 模拟人把框关了。
    await page.rerender({ modelValue: false })
    await vi.advanceTimersByTimeAsync(20000)
    expect(pollSubscriptionDeviceFlow).not.toHaveBeenCalled()
  })
})

describe('导入订阅 · 错误态', () => {
  it('start 失败：显示服务端原话，框不关，「重试」重发 flow', async () => {
    startSubscriptionDeviceFlow.mockRejectedValue(new Error('OpenAI 授权服务不可达：connection refused'))
    const page = mountDialog()

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.start' }))
    expect(await page.findByText(/connection refused/)).toBeTruthy()
    // 框没关（标题还在），且能重试。
    expect(page.getByText('models.subscription.dialogTitle')).toBeTruthy()

    startSubscriptionDeviceFlow.mockResolvedValue(FLOW)
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.retry' }))
    expect(await page.findByText('ABCD-EFGH')).toBeTruthy()
    expect(startSubscriptionDeviceFlow).toHaveBeenCalledTimes(2)
  })

  it('poll 失败：原话显示 + 停止轮询；「重试」恢复轮询而不是重发 flow', async () => {
    const page = mountDialog()
    await toWaiting(page)

    pollSubscriptionDeviceFlow.mockRejectedValue(new Error('网关 503：过会儿再试'))
    await vi.advanceTimersByTimeAsync(5000)
    expect(await page.findByText(/过会儿再试/)).toBeTruthy()
    await vi.advanceTimersByTimeAsync(20000)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledTimes(1)

    pollSubscriptionDeviceFlow.mockResolvedValue({ state: 'pending' })
    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.retry' }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(pollSubscriptionDeviceFlow).toHaveBeenCalledTimes(2)
    // 重发 flow 是另一条路（重新开始），不是这里。
    expect(startSubscriptionDeviceFlow).toHaveBeenCalledTimes(1)
  })
})

describe('导入订阅 · 完成', () => {
  it('「完成」关闭对话框并 emit imported（页面据此重拉列表）', async () => {
    const onImported = vi.fn()
    const page = mountDialog({ onImported })
    await toWaiting(page)
    pollSubscriptionDeviceFlow.mockResolvedValue({
      state: 'complete',
      subscription: { id: 'sub-1', status: 'active', account_email: 'admin@example.com' },
    })
    await vi.advanceTimersByTimeAsync(5000)
    await page.findByText('models.subscription.success')

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.done' }))
    expect(onImported).toHaveBeenCalledWith({ id: 'sub-1', status: 'active', account_email: 'admin@example.com' })
  })
})
