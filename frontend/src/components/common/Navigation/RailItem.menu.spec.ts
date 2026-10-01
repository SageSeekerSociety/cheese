// 右键 rail 上的一个项目：弹出那个项目能做的事。没有菜单的格子（首页、＋新建）
// 不拦右键。
import type { Component } from 'vue'
import type { NavGenericItem } from './types'

import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import RailItem from './RailItem.vue'

const Item = RailItem as unknown as Component
const vuetify = createVuetify({ components, directives })
const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:rest(.*)', component: { template: '<div />' } }],
})

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  })
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  // 菜单弹在一个点上时，Vuetify 的定位要读这两样；happy-dom 没有，真浏览器都有。
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
  if (!document.elementFromPoint) document.elementFromPoint = () => null
})

async function mount(item: NavGenericItem) {
  await router.push('/')
  await router.isReady()
  return render(Item, { props: { item }, global: { plugins: [vuetify, router] } })
}

function rightClick(el: Element): MouseEvent {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 120 })
  el.dispatchEvent(event)
  return event
}

describe('右键 rail 上的项目', () => {
  it('弹出这个项目的操作，点一项就做那一件事', async () => {
    const copy = vi.fn()
    const view = await mount({
      key: 'cx-p1',
      type: 'item',
      title: 'P1',
      to: '/projects/p1',
      img: 'data:image/svg+xml,<svg/>',
      projectId: 'p1',
      menu: [
        { key: 'copy', label: '复制链接', icon: 'mdi-link-variant', onSelect: copy },
        { key: 'leave', label: '退出项目', icon: 'mdi-exit-to-app', danger: true, onSelect: () => {} },
      ],
    })
    const event = rightClick(view.container.querySelector('.app-rail-item')!)
    expect(event.defaultPrevented).toBe(true)
    let entry: HTMLElement | undefined
    await vi.waitFor(() => {
      entry = Array.from(document.querySelectorAll<HTMLElement>('.v-overlay .v-list-item')).find((n) =>
        n.textContent?.includes('复制链接')
      )
      expect(entry).toBeTruthy()
    })
    expect(entry!.closest('.v-list')?.textContent).toContain('退出项目')
    await fireEvent.click(entry!)
    expect(copy).toHaveBeenCalledOnce()
  })

  it('没有菜单的格子不拦右键', async () => {
    const view = await mount({ key: 'Home', type: 'item', title: '首页', to: '/', icon: 'cheese' })
    expect(rightClick(view.container.querySelector('.app-rail-item')!).defaultPrevented).toBe(false)
  })
})
