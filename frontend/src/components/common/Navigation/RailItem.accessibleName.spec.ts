// 左侧项目栏的每个格子必须有「可访问名称」。
//
// 线上抓到的现象：`document.querySelectorAll('a[href^="/project/"]')` 出来的链接
// 全是 title=null / aria-label=null / innerText=""。项目格子渲染的是一个首字母
// 方块（没挑过头像时 UserAvatar 画的那个），12 个项目里有 4 个方块都是「机」——
// 读屏读不出来，用户也只能一个个点开试。
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

import { setLocale } from '@/i18n'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: { template: '<div />' } }],
})

function mount(item: NavGenericItem) {
  const vuetify = createVuetify({ components, directives })
  return render(RailItem, { props: { item }, global: { plugins: [vuetify, router] } })
}

beforeAll(() => {
  setLocale('zh-CN')
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
      projectId: 'p1',
      to: '/project/p1',
      // 没挑过头像 → 格子退成首字母方块（img 是空串，不是一张真头像）。
      img: '',
      shortcut: 2,
    })

    const link = container.querySelector('a[href^="/project/"]') as HTMLAnchorElement | null
    expect(link).not.toBeNull()
    // 方格里那一格首字母对读屏隐身（UserAvatar 默认 aria-hidden），链接本身没有可读
    // 文字——可访问名称只能来自 aria-label，线上就是它为 null。
    expect(link!.querySelector('.user-avatar-char')?.textContent?.trim()).toBe('知')
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

  it('首页那一格的件数角标画出来了，而且名字里也听得到', async () => {
    const { container } = mount({
      key: 'Home',
      type: 'item',
      title: '首页',
      to: '/inbox',
      icon: 'cheese',
      badge: 3,
    })

    const link = container.querySelector('a[href="/inbox"]') as HTMLAnchorElement | null
    expect(link).not.toBeNull()
    expect(link!.querySelector('.app-rail-item__badge')?.textContent).toBe('3')
    // 角标本身是 aria-hidden 的（画给眼睛），件数必须同时进可访问名称。
    expect(link!.getAttribute('aria-label')).toBe('首页（3）')
  })

  it('没有件数、只有没读的动态时，名字里听得到「有新动态」', async () => {
    const { container } = mount({
      key: 'Home',
      type: 'item',
      title: '首页',
      to: '/inbox',
      icon: 'cheese',
      badge: 0,
      dot: true,
    })

    const link = container.querySelector('a[href="/inbox"]') as HTMLAnchorElement | null
    expect(link!.querySelector('.app-rail-item__badge')).toBeNull()
    expect(link!.getAttribute('aria-label')).toBe('首页（有新动态）')
  })

  it('什么都没有时名字里不多那对括号', async () => {
    const { container } = mount({
      key: 'Home',
      type: 'item',
      title: '首页',
      to: '/inbox',
      icon: 'cheese',
      badge: 0,
    })

    const link = container.querySelector('a[href="/inbox"]') as HTMLAnchorElement | null
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
