import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Landing from './Landing.vue'

import i18n, { resolveInitialLocale, setLocale } from '@/i18n'
import HomeRoutes from '@/router/home'
import AccountService from '@/services/account'

vi.mock('@/services/account', () => ({ default: reactive({ loggedIn: false }) }))
// happy-dom has no IntersectionObserver; the scroll-driven room simply stays on its first step.
vi.stubGlobal(
  'IntersectionObserver',
  class {
    observe() {}
    disconnect() {}
  }
)
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
  const view = render(
    { template: '<router-view />' },
    { global: { plugins: [router, createVuetify({ components, directives })] } }
  )
  return { ...view, router }
}

describe('公开首页', () => {
  it('switches to English without losing the chosen solution, and remembers the language', async () => {
    const view = await mount('/about')
    await fireEvent.click(view.getByRole('tab', { name: '企业' }))
    await fireEvent.click(view.getByRole('button', { name: 'Switch to English' }))
    expect(document.documentElement.lang).toBe('en')
    expect(resolveInitialLocale()).toBe('en')
    expect(view.getByRole('tab', { name: 'Companies' }).getAttribute('aria-selected')).toBe('true')
    for (const link of view.getAllByRole('link', { name: /Get started/ })) {
      expect(link.getAttribute('href')).toBe('/account/signin')
    }
    await fireEvent.click(view.getByRole('button', { name: '切换到中文' }))
    expect(i18n.global.locale.value).toBe('zh-CN')
  })

  it('moves between solutions with the arrow keys, and the panel follows the selected tab', async () => {
    const view = await mount()
    const first = view.getByRole('tab', { name: '高校与机构' })
    expect(first.getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(first, { key: 'ArrowRight' })
    const company = view.getByRole('tab', { name: '企业' })
    expect(company.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(company)
    await waitFor(() =>
      expect(view.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(company.getAttribute('id'))
    )
    await fireEvent.keyDown(company, { key: 'End' })
    expect(view.getByRole('tab', { name: '科研与创新团队' }).getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    expect(first.getAttribute('aria-selected')).toBe('true')
  })

  it('keeps the introduction open to signed-in users and links back to work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/about')
    expect(view.router.currentRoute.value.path).toBe('/about')
    const links = view.getAllByRole('link', { name: /进入工作台/ })
    expect(links.length).toBeGreaterThan(0)
    for (const link of links) expect(link.getAttribute('href')).toBe('/')
    await view.router.push(links[0].getAttribute('href')!)
    expect(view.router.currentRoute.value.name).toBe('HomeWork')
  })

  it('updates the entry links after session restoration without navigating away', async () => {
    const view = await mount('/about')
    const signedOut = view.getAllByRole('link', { name: /开始使用/ })
    for (const link of signedOut) expect(link.getAttribute('href')).toBe('/account/signin')
    AccountService.loggedIn = true
    await waitFor(() => expect(view.getAllByRole('link', { name: /进入工作台/ })).toHaveLength(signedOut.length))
    expect(view.router.currentRoute.value.path).toBe('/about')
  })

  it('shows the public homepage when a signed-out user returns from work', async () => {
    AccountService.loggedIn = true
    const view = await mount('/')
    expect(view.router.currentRoute.value.name).toBe('HomeWork')
    AccountService.loggedIn = false
    await view.router.push('/')
    expect(view.router.currentRoute.value.path).toBe('/')
    expect(view.getAllByRole('link', { name: /开始使用/ }).length).toBeGreaterThan(0)
  })
})
