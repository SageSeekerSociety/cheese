// 一页的操作在手机上不单占一行，交给顶栏：主操作是顶栏上一颗按钮，其余的在顶栏的
// 「更多操作」里。点哪一颗都执行的是页面上那一个操作；页面走了，它的操作也跟着从
// 顶栏上走。桌面上操作照旧画在页面里。
import { defineComponent, h, ref } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import MobileAppBar from './Navigation/MobileAppBar.vue'
import PageAction from './PageAction.vue'

import i18n, { t } from '@/i18n'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, hot_min_items: 5 })),
}))

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

async function mount(width: number) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const onNew = vi.fn()
  const onRefresh = vi.fn()
  const showPage = ref(true)
  const Page = defineComponent({
    setup: () => () =>
      h('main', [
        h(PageAction, { label: '刷新', icon: 'mdi-refresh', onClick: onRefresh }),
        h(PageAction, { label: '新建', icon: 'mdi-plus', primary: true, onClick: onNew }),
      ]),
  })
  const Host = defineComponent({
    setup: () => () => h(components.VApp, null, () => [h(MobileAppBar), showPage.value ? h(Page) : null]),
  })
  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: '/:p(.*)*', component: { template: '<div />' }, meta: { title: '定时与触发', backTo: 'x' } }],
  })
  await router.push('/routines')
  await router.isReady()
  const view = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
  return { ...view, onNew, onRefresh, showPage }
}

const bar = () => document.querySelector('header.v-app-bar, .v-app-bar') as HTMLElement
const page = () => document.querySelector('main') as HTMLElement

describe('PageAction', () => {
  it('手机上：主操作在顶栏上，其余在「更多操作」里，点了都执行', async () => {
    const { onNew, onRefresh } = await mount(390)
    await waitFor(() => expect(within(bar()).getByRole('button', { name: '新建' })).toBeTruthy())
    expect(within(page()).queryByRole('button')).toBeNull()

    await fireEvent.click(within(bar()).getByRole('button', { name: '新建' }))
    expect(onNew).toHaveBeenCalledTimes(1)

    expect(within(bar()).queryByRole('button', { name: '刷新' })).toBeNull()
    await fireEvent.click(within(bar()).getByRole('button', { name: t('navigation.shell.more') }))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '刷新' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('手机上：页面走了，它的操作也从顶栏上走', async () => {
    const { showPage } = await mount(390)
    await waitFor(() => expect(within(bar()).getByRole('button', { name: '新建' })).toBeTruthy())
    showPage.value = false
    await waitFor(() => expect(within(bar()).queryByRole('button', { name: '新建' })).toBeNull())
    expect(within(bar()).queryByRole('button', { name: t('navigation.shell.more') })).toBeNull()
  })

  it('桌面上：操作画在页面里，顶栏上没有', async () => {
    const { onRefresh } = await mount(1280)
    await fireEvent.click(within(page()).getByRole('button', { name: '刷新' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(within(bar()).queryByRole('button', { name: '新建' })).toBeNull()
  })
})
