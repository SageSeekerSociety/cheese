/**
 * 「上架模型」对话框：账号可用清单的勾选与整集替换。
 *
 * 钉三件事：
 *
 * 1. **勾选状态来自服务端**。`shelved` 标记是初始勾选 —— 打开就勾好已上架的，
 *    不是打开全不勾（那样保存一次等于全部下架，正好点反）。
 * 2. **保存是整集替换**。PUT 的 `models` 恰好是勾选的那些（带 slug 与显示名），
 *    一个不多一个不少；全部取消勾选发的是空数组（全部下架的合法写法）。
 * 3. **失败显示服务端原话，框不关**。409（名字撞了在服模型）/ 503（网关不可达）
 *    改写成「保存失败」就是把原因丢了。
 *
 * 模子照 `AdminSubscriptionImportDialog.spec.ts`。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getSubscriptionAvailableModels = vi.fn()
const putSubscriptionModels = vi.fn()

vi.mock('@/api', () => ({
  getSubscriptionAvailableModels: (...a: unknown[]) => getSubscriptionAvailableModels(...a),
  putSubscriptionModels: (...a: unknown[]) => putSubscriptionModels(...a),
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
  }),
}))

import AdminSubscriptionModelsDialog from './AdminSubscriptionModelsDialog.vue'

const AVAILABLE = [
  { slug: 'gpt-6-astra', display_name: 'GPT-6-Astra', description: 'Frontier.', priority: 1, shelved: false },
  { slug: 'gpt-5.6-sol', display_name: 'GPT-5.6-Sol', description: '', priority: 4, shelved: false },
  { slug: 'gpt-5.6-luna', display_name: 'GPT-5.6-Luna', description: '', priority: 8, shelved: true },
]

function mountDialog() {
  const vuetify = createVuetify({ components, directives })
  const saved = vi.fn()
  const closed = vi.fn()
  const Wrapper = {
    components: { AdminSubscriptionModelsDialog },
    template: '<v-app><AdminSubscriptionModelsDialog v-bind="$attrs" /></v-app>',
    inheritAttrs: false,
  }
  const page = render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify] },
    props: { modelValue: true, subscriptionId: 'sub-1', onSaved: saved, 'onUpdate:modelValue': closed },
  })
  return Object.assign(page, { saved, closed })
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
  // 对话框浮层定位要读 visualViewport。
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
  getSubscriptionAvailableModels.mockResolvedValue({ items: AVAILABLE.map((i) => ({ ...i })) })
  putSubscriptionModels.mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('上架模型 · 勾选与整集替换', () => {
  it('打开按 shelved 预勾，保存恰好发勾选的那些', async () => {
    const page = mountDialog()
    await page.findByText('GPT-6-Astra')

    // luna 已预勾（shelved: true）；再勾上 astra，sol 不动。
    const boxes = page.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes.map((b) => b.checked)).toEqual([false, false, true])
    boxes[0].checked = true
    await fireEvent.change(boxes[0])

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.shelve.save' }))
    expect(putSubscriptionModels).toHaveBeenCalledWith('sub-1', [
      { upstream_model: 'gpt-6-astra', label: 'GPT-6-Astra' },
      { upstream_model: 'gpt-5.6-luna', label: 'GPT-5.6-Luna' },
    ])
  })

  it('全部取消勾选发空数组（全部下架）', async () => {
    const page = mountDialog()
    await page.findByText('GPT-6-Astra')

    const boxes = page.getAllByRole('checkbox') as HTMLInputElement[]
    boxes[2].checked = false // 取消已预勾的 luna
    await fireEvent.change(boxes[2])

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.shelve.save' }))
    expect(putSubscriptionModels).toHaveBeenCalledWith('sub-1', [])
  })

  it('保存成功 emit saved 并关框', async () => {
    const page = mountDialog()
    await page.findByText('GPT-6-Astra')

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.shelve.save' }))
    await vi.waitFor(() => expect(page.saved).toHaveBeenCalled())
    expect(page.closed).toHaveBeenCalledWith(false)
  })

  it('失败显示服务端原话，框不关、不发 saved', async () => {
    putSubscriptionModels.mockRejectedValue(new Error('ConflictError: 模型名 gpt-5.6-luna 在网关上已存在'))
    const page = mountDialog()
    await page.findByText('GPT-6-Astra')

    await fireEvent.click(page.getByRole('button', { name: 'models.subscription.shelve.save' }))
    await page.findByText(/模型名 gpt-5.6-luna 在网关上已存在/)
    expect(page.saved).not.toHaveBeenCalled()
  })

  it('清单加载失败显示原话', async () => {
    getSubscriptionAvailableModels.mockRejectedValue(new Error('GatewayUnavailableError: 模型清单接口不可达'))
    const page = mountDialog()
    await page.findByText(/模型清单接口不可达/)
    expect(putSubscriptionModels).not.toHaveBeenCalled()
  })
})
