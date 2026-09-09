import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Landing from './Landing.vue'

import i18n, { resolveInitialLocale, setLocale } from '@/i18n'
import HomeRoutes from '@/router/home'
import AccountService from '@/services/account'

vi.mock('@/services/account', () => ({ default: reactive({ loggedIn: false }) }))
beforeEach(() => setLocale('zh-CN'))

afterEach(() => {
  cleanup()
  AccountService.loggedIn = false
})

async function mount(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: HomeRoutes.children!.map((route) => ({
      path: `/${route.path}`,
      name: route.name,
      meta: route.meta,
      beforeEnter: route.beforeEnter,
      component: route.meta?.publicLanding ? Landing : { template: '<div>Workspace</div>' },
    })),
  })
  await router.push(path)
  const view = render({ template: '<router-view />' }, { global: { plugins: [router], stubs: { VIcon: true } } })
  return { ...view, router }
}

describe('公开首页', () => {
  it('switches the whole demo to English without resetting the selected audience and remembers the choice', async () => {
    const view = await mount('/about')
    await fireEvent.click(view.getByRole('tab', { name: '高校与机构' }))
    await fireEvent.click(view.getByRole('button', { name: 'Switch to English' }))
    expect(document.documentElement.lang).toBe('en')
    expect(resolveInitialLocale()).toBe('en')
    expect(view.getByRole('heading', { name: 'Learn through real projects.' })).toBeTruthy()
    expect(view.getByRole('tab', { name: 'Universities and institutions' }).getAttribute('aria-selected')).toBe('true')
    await fireEvent.click(view.getByRole('tab', { name: /03\s*Test/ }))
    await fireEvent.click(view.getByRole('button', { name: 'Show sample answer' }))
    expect(view.getByText('Sample source: Project getting started guide, section 1')).toBeTruthy()
    await fireEvent.click(view.getByRole('tab', { name: /04\s*Deliver/ }))
    await fireEvent.click(view.getByRole('button', { name: 'Show sample result' }))
    expect(view.getByText(/The prototype shows source citations/)).toBeTruthy()
    for (const link of view.getAllByRole('link', { name: 'Get started' })) {
      expect(link.getAttribute('href')).toBe('/account/signin')
    }
    await fireEvent.click(view.getByRole('button', { name: '切换到中文' }))
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(view.getByRole('heading', { name: '让实践育人，发生在真实项目里。' })).toBeTruthy()
  })

  it('可用键盘走完整个项目演示并打开成果示例', async () => {
    const view = await mount()
    const first = view.getByRole('tab', { name: /01\s*需求讨论/ })
    await fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(view.getByRole('tab', { name: /02\s*协作执行/ }).getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    await fireEvent.click(view.getByRole('button', { name: '查看回答示例' }))
    expect(view.getByText('来源示例：项目入门指南 §1')).toBeTruthy()
    await fireEvent.click(view.getByRole('tab', { name: /04\s*成果交付/ }))
    await fireEvent.click(view.getByRole('button', { name: '查看成果示例' }))
    expect(view.getByText(/原型展示资料引用位置/)).toBeTruthy()
  })

  it('企业解决方案默认可见，也可切换到高校场景', async () => {
    const view = await mount()
    expect(view.getByRole('heading', { name: '让团队把 AI 用进真实项目。' })).toBeTruthy()
    await fireEvent.click(view.getByRole('tab', { name: '高校与机构' }))
    expect(view.getByRole('heading', { name: '让实践育人，发生在真实项目里。' })).toBeTruthy()
  })

  it('会话恢复成功后进入用户空间', async () => {
    const { router } = await mount()
    AccountService.loggedIn = true
    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaces'))
  })

  it('keeps the introduction accessible to signed-in users and links back to work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/about')
    expect(view.router.currentRoute.value.path).toBe('/about')
    expect(view.getByRole('heading', { level: 1 }).textContent).toContain('真正')
    const links = view.getAllByRole('link', { name: '进入工作台' })
    expect(links).toHaveLength(3)
    for (const link of links) expect(link.getAttribute('href')).toBe('/')
    await view.router.push(links[0].getAttribute('href')!)
    expect(view.router.currentRoute.value.name).toBe('HomeSpaces')
  })

  it('updates the introduction actions after session restoration without navigating away', async () => {
    const view = await mount('/about')
    for (const link of view.getAllByRole('link', { name: '开始体验' })) {
      expect(link.getAttribute('href')).toBe('/account/signin')
    }
    AccountService.loggedIn = true
    await waitFor(() => expect(view.getAllByRole('link', { name: '进入工作台' })).toHaveLength(3))
    expect(view.router.currentRoute.value.path).toBe('/about')
  })

  it('shows the public homepage when a signed-out user returns from work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/')
    expect(view.router.currentRoute.value.name).toBe('HomeSpaces')
    AccountService.loggedIn = false
    await view.router.push('/')
    expect(view.router.currentRoute.value.path).toBe('/')
    expect(view.getAllByRole('link', { name: '开始体验' })).toHaveLength(3)
  })
})
