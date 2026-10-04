/** 动态（通知）列表读失败：整块换成失败块（§3.10），给服务端原话和一条重试。
 *
 *  以前第一页读失败只留一句 console.error，屏幕上接着画「暂无通知」——和「本来就没
 *  有」一模一样。401/403 说没权限、不给重试。
 */
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()

vi.mock('@/network/api/notifications', async () => {
  const actual = await vi.importActual<typeof import('@/network/api/notifications')>('@/network/api/notifications')
  return { ...actual, NotificationsApi: { ...actual.NotificationsApi, list: (...a: unknown[]) => list(...a) } }
})

// 未读数那一位和这一份用例无关：给一个空实现，免得它去碰真接口。
vi.mock('@/composables/useUnreadNotifications', () => ({
  useUnreadNotifications: () => ({ count: ref(0), refresh: vi.fn(), set: vi.fn() }),
}))

// 组件通过 `useI18n()` 取词，而 `@/i18n` 不导出那个 i18n 实例、没法 `app.use`。
// 只把取词入口换成 `@/i18n` 的全局 `t`，这样 `setLocale('zh-CN')` 依旧管用。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  const { t } = await vi.importActual<typeof import('@/i18n')>('@/i18n')
  return { ...actual, useI18n: () => ({ t }) }
})

import NotificationFeed from './NotificationFeed.vue'

import { ApiError } from '@/api'
import { setLocale, t } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }],
})

function emptyPage() {
  return { data: { notifications: [], page: { hasMore: false, nextStart: undefined } } }
}

beforeEach(() => {
  list.mockReset()
})

function mount() {
  const vuetify = createVuetify({ components, directives })
  return render(NotificationFeed, { global: { plugins: [vuetify, router, createPinia()] } })
}

describe('动态列表读失败', () => {
  it('整块换成失败块：服务端原话 + 重试，不显示「暂无通知」', async () => {
    list.mockRejectedValue(new Error('HTTP 500 for /notifications'))
    mount()
    expect(await screen.findByText(t('notifications.common.loadFailed'))).toBeTruthy()
    expect(screen.getByText('HTTP 500 for /notifications')).toBeTruthy()
    expect(screen.queryByText(t('notifications.common.noNotifications')), '失败不能显示成「暂无通知」').toBeNull()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
  })

  it('点重试，重新读一次：失败块收掉', async () => {
    list.mockRejectedValueOnce(new Error('boom'))
    mount()
    const retry = await screen.findByRole('button', { name: t('global.loadError.retry') })
    list.mockResolvedValue(emptyPage())
    await fireEvent.click(retry)
    await waitFor(() => expect(screen.queryByText(t('notifications.common.loadFailed'))).toBeNull())
  })

  it('401/403：说没权限，不给重试', async () => {
    list.mockRejectedValue(new ApiError(403, 'forbidden'))
    mount()
    expect(await screen.findByText(t('global.loadError.forbidden'))).toBeTruthy()
    expect(screen.queryByRole('button', { name: t('global.loadError.retry') }), '没权限不该给重试').toBeNull()
  })

  it('确实没有通知时，还是「暂无通知」', async () => {
    list.mockResolvedValue(emptyPage())
    mount()
    expect(await screen.findByText(t('notifications.common.noNotifications'))).toBeTruthy()
    expect(screen.queryByText(t('notifications.common.loadFailed'))).toBeNull()
  })
})
