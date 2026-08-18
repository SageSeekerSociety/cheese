// 汉堡只在真的有抽屉的页面出现。
//
// 手机上的二级侧栏正在一页一页退成页内分段（设计文档 §3.3），退完的页面下面
// 已经没有抽屉了 —— 顶栏却看不见这件事，照样画一个汉堡，点下去什么也不发生。
// /inbox 从落地那天起就是这个样子：它根本没有侧栏。所以由路由自己声明。
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

import MobileAppBar from './MobileAppBar.vue'

import i18n from '@/i18n'
import { useNavigationStore } from '@/stores/navigation'

const blank = { template: '<div />' }
const routes = [
  { path: '/inbox', name: 'inbox', component: blank },
  // 没登录的时候这条顶栏会把人送去登录页；路由表里缺了它只会刷一行警告。
  { path: '/account/signin', name: 'signin', component: blank },
  { path: '/spaces/:spaceId', name: 'space', component: blank, meta: { drawer: true } },
]

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

async function mountAt(path: string) {
  const router = createRouter({ history: createWebHistory(), routes })
  await router.push(path)
  await router.isReady()
  const Host = {
    components: { MobileAppBar },
    template: '<v-layout><MobileAppBar /></v-layout>',
  }
  const pinia = createPinia()
  const utils = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] },
  })
  return { ...utils, pinia }
}

const hamburger = (container: Element) => container.querySelector('.v-app-bar-nav-icon')

describe('手机顶栏的汉堡', () => {
  it('页面下面挂着抽屉时才画它，点它开抽屉', async () => {
    const { container, pinia } = await mountAt('/spaces/s1')
    const button = hamburger(container)
    expect(button).toBeTruthy()

    await fireEvent.click(button!)
    expect(useNavigationStore(pinia).isSecondaryDrawerOpen).toBe(true)
  })

  it('没有抽屉的页面不画它', async () => {
    const { container } = await mountAt('/inbox')
    expect(hamburger(container)).toBeNull()
  })
})
