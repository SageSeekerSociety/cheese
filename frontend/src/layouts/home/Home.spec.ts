// 首页这一层在手机上没有抽屉：空间和小队是顶栏里的两格分段，直接点得到。
//
// 它们以前只住在 HomeSidebar 那条抽屉里，而底栏已经是一层常驻 chrome —— 于是
// 「小队」在手机上唯一的入口是左上角那个汉堡。这里测的就是那个入口回到了顶栏上，
// 并且**只在手机上**：桌面那条常驻侧栏还在，再画一份分段就是同一份清单画两遍。
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import Home from './Home.vue'

const routes = [
  {
    path: '/',
    component: Home,
    children: [
      { path: 'spaces', name: 'HomeSpaces', component: { template: '<div>空间列表</div>' } },
      { path: 'teams', name: 'HomeTeams', component: { template: '<div>小队列表</div>' } },
    ],
  },
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

async function mountAt(width: number, path: string) {
  // 顶栏那一格的落点。真实环境里它由 MobileAppBar 画，这里只需要它存在，
  // 否则 Teleport 无处可去。
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  window.innerWidth = width
  window.innerHeight = 844
  const router = createRouter({ history: createWebHistory(), routes })
  await router.push(path)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  return render(Home, { global: { plugins: [vuetify, router] } })
}

describe('首页外框', () => {
  it('手机上两格分段填进顶栏那一格，点得到小队', async () => {
    const { findByText } = await mountAt(390, '/spaces')
    const slot = document.getElementById('app-bar-slot')!
    expect(await findByText('空间列表')).toBeTruthy()

    // v-tabs 把每一格渲染两遍（一份用来量宽度），所以取第一个。
    const tabs = Array.from(slot.querySelectorAll('a'))
    expect(tabs.map((a) => a.textContent?.trim())).toContain('小队')
    await fireEvent.click(tabs.find((a) => a.textContent?.includes('小队'))!)
    expect(await findByText('小队列表')).toBeTruthy()
  })

  it('桌面上不画这一份 —— 清单在常驻侧栏里', async () => {
    const { findByText } = await mountAt(1280, '/spaces')
    expect(await findByText('空间列表')).toBeTruthy()
    expect(document.getElementById('app-bar-slot')!.textContent).toBe('')
  })
})
