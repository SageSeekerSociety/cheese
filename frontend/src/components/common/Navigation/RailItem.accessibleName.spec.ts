// 左侧项目栏的每个格子必须有「可访问名称」。
//
// 线上抓到的现象：`document.querySelectorAll('a[href^="/project/"]')` 出来的链接
// 全是 title=null / aria-label=null / innerText=""。项目格子渲染的是首字方块
// （projectAvatar(p.name)），12 个项目里有 4 个方块都是「机」——读屏读不出来，
// 用户也只能一个个点开试。
//
// App.vue 早就把 `title: p.name` 放进 rail item 了，漏的是渲染层没把它落到 <a>
// 上。所以这里测的是「渲染层有没有把 title 落成可访问名称」，不是数据构造。
import type { NavGenericItem } from './types'

import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import RailItem from './RailItem.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: { template: '<div />' } }],
})

function mount(item: NavGenericItem) {
  const vuetify = createVuetify({ components, directives })
  return render(RailItem, { props: { item }, global: { plugins: [vuetify, router] } })
}

beforeAll(() => {
  // Vuetify 的 overlay（v-tooltip）会摸这两个浏览器 API，happy-dom 没有。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
})

describe('RailItem 的可访问名称', () => {
  it('项目格子（只有头像、没有文字）的链接带 aria-label', async () => {
    const { container } = mount({
      key: 'cx-1',
      type: 'item',
      title: '知是 2.0 融合演示',
      to: '/project/p1',
      img: 'data:image/svg+xml,<svg/>',
      shortcut: 2,
    })

    const link = container.querySelector('a[href^="/project/"]') as HTMLAnchorElement | null
    expect(link).not.toBeNull()
    // 这一格没有任何可见文字，可访问名称只能来自 aria-label——线上就是它为 null。
    expect(link!.textContent?.trim()).toBe('')
    expect(link!.getAttribute('aria-label')).toBe('知是 2.0 融合演示')
  })

  it('图标格子的链接也带 aria-label', async () => {
    const { container } = mount({
      key: 'Home',
      type: 'item',
      title: '首页',
      to: '/',
      icon: 'cheese',
      shortcut: 1,
    })

    const link = container.querySelector('a[href="/"]') as HTMLAnchorElement | null
    expect(link).not.toBeNull()
    expect(link!.getAttribute('aria-label')).toBe('首页')
  })

  it('悬停浮层里带项目全名（读屏之外，鼠标用户靠它认项目）', async () => {
    const { container } = mount({
      key: 'cx-1',
      type: 'item',
      title: '知是 2.0 融合演示',
      to: '/project/p1',
      img: 'data:image/svg+xml,<svg/>',
      shortcut: 2,
    })

    // v-tooltip 的内容渲染在 body 的 overlay 容器里，不在 container 内。
    expect(container.querySelector('a[href^="/project/"]')).not.toBeNull()
    expect(document.body.textContent).toContain('知是 2.0 融合演示')
  })
})
