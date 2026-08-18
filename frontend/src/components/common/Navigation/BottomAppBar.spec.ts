// 底栏渲染的是「手机那份清单」，一格不多一格不少。
//
// 之前底栏自己拿 visibleOnMobile 过滤全量清单，于是每多一个项目就多一格
// （五个项目 = 七格，每格约 53px），而「＋新建项目」被同一个开关关掉之后
// 手机上就建不了项目了。所以这里测两件事：格子来自清单本身，以及**没有地址、
// 只有动作**的那种格子点得动——工作区那一格在你一个项目都没有时就是它。
import type { NavItem } from './types'

import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import BottomAppBar from './BottomAppBar.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: { template: '<div />' } }],
})

function mount(items: NavItem[]) {
  const vuetify = createVuetify({ components, directives })
  // v-bottom-navigation 要坐在一个 layout 里，和它在 App.vue 里的位置一样。
  const Host = {
    components: { BottomAppBar },
    props: ['items'],
    template: '<v-layout><BottomAppBar :items="items" /></v-layout>',
  }
  return render(Host, { props: { items }, global: { plugins: [vuetify, router] } })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

const tab = (title: string, extra: Partial<NavItem> = {}): NavItem => ({
  key: title,
  type: 'item',
  title,
  icon: 'mdi-circle',
  ...extra,
})

describe('BottomAppBar', () => {
  it('清单里有几格就渲染几格', async () => {
    const { findByText, queryByText } = mount([
      tab('空间', { to: '/spaces' }),
      tab('工作区', { to: '/projects/p1' }),
      tab('待办', { to: '/inbox' }),
    ])
    for (const label of ['空间', '工作区', '待办']) expect(await findByText(label)).toBeTruthy()
    expect(queryByText('新建项目')).toBeNull()
  })

  it('只有动作、没有地址的一格点得动', async () => {
    const createProject = vi.fn()
    const { findByText } = mount([tab('工作区', { action: createProject })])
    await fireEvent.click(await findByText('工作区'))
    expect(createProject).toHaveBeenCalledOnce()
  })

  it('一格底下不止一条顶层路由时，它照样亮着', async () => {
    // 「空间」那一格装的是首页整层（空间 + 小队），而 /teams 不在 /spaces 底下。
    // 只按 router-link 的规则算，人在小队里翻的时候底栏整排都是灰的——看起来
    // 像已经走出了这个 app 的导航。
    await router.push('/teams/explore')
    const { container } = mount([
      tab('空间', { to: '/spaces', match: (path) => path.startsWith('/teams') }),
      tab('待办', { to: '/inbox' }),
    ])
    const lit = [...container.querySelectorAll('.v-btn--active')].map((el) => el.textContent?.trim())
    expect(lit).toEqual(['空间'])
  })
})
