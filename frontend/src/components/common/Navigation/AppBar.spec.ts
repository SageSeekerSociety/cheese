// 桌面顶栏（`AppBar`）画什么，由**外壳传给它的登录态**说了算。
//
// 钉的是那条产品规则：没登录的人语言开关留在顶栏上（登录之后那个开关搬进「我」的
// 菜单里，顶栏不再画它）。顶栏在 `src/components/**` 下，不能自己去读账号服务
// （`.claude/rules/architecture.md`），所以这一条能且只能听 prop —— 这两个用例给
// 顶栏的就是外壳会给它的那两种状态。
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import AppBar from './AppBar.vue'

import i18n, { setLocale } from '@/i18n'
import { closeFirstPaint } from '@/test/firstPaint'

setLocale('zh-CN')

// 顶栏里的帮助入口会去问未读数和自己的管理员身份；这两个回答跟这一组用例无关。
vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, is_platform_admin: false, tabs: [], kinds: [] })),
}))

const blank = { template: '<div />' }

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

async function mount(loggedIn: boolean) {
  // 首帧那道门关着：这一组用例看的是顶栏的形状，不需要它去问任何东西。
  closeFirstPaint()
  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: '/', name: 'home', component: blank, meta: { title: '首页' } }],
  })
  await router.push('/')
  await router.isReady()
  // 挂在 `v-layout` 里：顶栏是一条 `v-system-bar`，它要问注入进来的布局算自己的高度
  //（同 `MobileAppBar.slot.spec.ts` 那份）。
  const Host = {
    components: { AppBar },
    setup: () => ({ loggedIn }),
    template: '<v-layout><AppBar :logged-in="loggedIn" /></v-layout>',
  }
  return render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
}

describe('桌面顶栏', () => {
  it('没登录的人语言开关留在顶栏上', async () => {
    const { container } = await mount(false)
    expect(container.querySelector('.language-toggle')).not.toBeNull()
  })

  it('登录之后顶栏不再画语言开关：它在「我」的菜单里', async () => {
    const { container } = await mount(true)
    expect(container.querySelector('.language-toggle')).toBeNull()
  })
})
