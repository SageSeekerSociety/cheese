// 一页的操作只写一次：桌面上画在这一页的页头里，手机上交给顶栏——主操作是顶栏上
// 一颗按钮，其余的在顶栏的「更多操作」里。点哪一颗都执行的是页面上那一件事；页面
// 走了（包括被 keep-alive 收起来），它的操作也跟着走。
import type { Command } from '.'

import { defineComponent, h, KeepAlive, ref } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { useCommands } from '.'

import AppPage from '@/components/common/AppPage.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'
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

/** 一页：页头上有「刷新」和主操作「新建」。 */
function pageWith(onNew: () => void, onRefresh: () => void, name = 'routines') {
  return defineComponent({
    name,
    setup() {
      useCommands((): Command[] => [
        { id: `${name}.refresh`, title: '刷新', icon: 'mdi-refresh', header: {}, run: onRefresh },
        { id: `${name}.new`, title: '新建', icon: 'mdi-plus', header: { primary: true, accent: true }, run: onNew },
      ])
      return () => h(AppPage, { title: '定时与触发' }, () => h('p', '正文'))
    },
  })
}

const Other = defineComponent({ name: 'Other', setup: () => () => h('p', '别的页') })

async function mount(width: number, { keepAlive = false } = {}) {
  ;(window as unknown as { innerWidth: number }).innerWidth = width
  const onNew = vi.fn()
  const onRefresh = vi.fn()
  const showPage = ref(true)
  const Page = pageWith(onNew, onRefresh)
  const Host = defineComponent({
    setup: () => () =>
      h(components.VApp, null, () => [
        h(MobileAppBar),
        h('main', [
          keepAlive ? h(KeepAlive, null, [showPage.value ? h(Page) : h(Other)]) : showPage.value ? h(Page) : null,
        ]),
      ]),
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

describe('页头上的命令', () => {
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

  it('手机上：被 keep-alive 收起来的页面，操作也不留在顶栏上；切回来又在', async () => {
    const { showPage } = await mount(390, { keepAlive: true })
    await waitFor(() => expect(within(bar()).getByRole('button', { name: '新建' })).toBeTruthy())
    showPage.value = false
    await waitFor(() => expect(within(bar()).queryByRole('button', { name: '新建' })).toBeNull())
    showPage.value = true
    await waitFor(() => expect(within(bar()).getByRole('button', { name: '新建' })).toBeTruthy())
  })

  // 桌面上没有手机顶栏（App 只在手机上挂它），操作就画在这一页的页头里。
  it('桌面上：操作画在页头里，点了执行', async () => {
    const { onRefresh, onNew } = await mount(1280)
    await fireEvent.click(within(page()).getByRole('button', { name: '刷新' }))
    await fireEvent.click(within(page()).getByRole('button', { name: '新建' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    expect(onNew).toHaveBeenCalledTimes(1)
  })
})
