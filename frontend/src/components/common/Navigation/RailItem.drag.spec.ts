// rail 上的项目格子可以拖着换顺序。这一份守的是格子报告了什么动作——从哪一格拖到
// 哪一格的哪一边——不是它内部存了什么。顺序本身怎么算在 lib/projectOrder.spec.ts。
import type { Component } from 'vue'
import type { NavGenericItem } from './types'

import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, describe, expect, it } from 'vitest'

import RailItem from './RailItem.vue'

const Item = RailItem as unknown as Component
const DRAG_TYPE = 'application/x-cheese-project'

const vuetify = createVuetify({ components, directives })
const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:rest(.*)', component: { template: '<div />' } }],
})

/**
 * 自己造 dataTransfer，并且直接 dispatchEvent 而不走 fireEvent：happy-dom 的
 * DragEvent 构造器既收不下 dataTransfer（它自己的 DataTransfer.types 是只读的），
 * 也丢掉 clientY——而落点在上半边还是下半边正是靠 clientY 判的。
 */
function dataTransfer() {
  const store = new Map<string, string>()
  return {
    get types() {
      return [...store.keys()]
    },
    effectAllowed: '',
    dropEffect: '',
    setData: (type: string, value: string) => void store.set(type, value),
    getData: (type: string) => store.get(type) ?? '',
  }
}

function drag(el: Element, type: string, props: Record<string, unknown>) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  for (const [key, value] of Object.entries(props)) {
    Object.defineProperty(event, key, { value, configurable: true })
  }
  el.dispatchEvent(event)
}

const tile = (id: string): NavGenericItem => ({
  key: `cx-${id}`,
  type: 'item',
  title: id.toUpperCase(),
  to: `/projects/${id}`,
  img: 'data:image/svg+xml,<svg/>',
  projectId: id,
})

const home: NavGenericItem = { key: 'Home', type: 'item', title: '首页', to: '/', icon: 'cheese' }

async function mount(item: NavGenericItem, props: Record<string, unknown> = {}) {
  await router.push('/')
  await router.isReady()
  const view = render(Item, { props: { item, ...props }, global: { plugins: [vuetify, router] } })
  return view
}

/** 让格子在测试里有真实的几何，否则上下半边分不出来（happy-dom 一律返回 0）。 */
function occupy(el: Element, top: number, height: number) {
  el.getBoundingClientRect = () =>
    ({ top, height, bottom: top + height, left: 0, right: 48, width: 48, x: 0, y: top, toJSON: () => ({}) }) as DOMRect
}

beforeAll(() => {
  // v-tooltip 的 activator 要它
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  })
})

describe('rail 项目格子的拖拽', () => {
  it('拖起来时把项目 id 交给拖拽，而不是让浏览器去拖那条链接', async () => {
    const view = await mount(tile('a'))
    const card = view.container.querySelector('.app-rail-item')!
    const dt = dataTransfer()

    drag(card, 'dragstart', { dataTransfer: dt })

    expect(dt.getData(DRAG_TYPE)).toBe('a')
    expect(dt.effectAllowed).toBe('move')
    expect(view.emitted().dragStart).toEqual([['a']])
  })

  it('落在上半边是插在前面，下半边是插在后面', async () => {
    const view = await mount(tile('b'))
    const card = view.container.querySelector('.app-rail-item')!
    occupy(card, 100, 40)
    const dt = dataTransfer()
    dt.setData(DRAG_TYPE, 'a')

    drag(card, 'dragover', { dataTransfer: dt, clientY: 110 })
    drag(card, 'dragover', { dataTransfer: dt, clientY: 130 })

    expect(view.emitted().dragOver).toEqual([
      ['b', 'before'],
      ['b', 'after'],
    ])
  })

  it('不接从 rail 外面拖进来的东西', async () => {
    const view = await mount(tile('b'))
    const card = view.container.querySelector('.app-rail-item')!
    const stranger = dataTransfer()
    stranger.setData('text/uri-list', 'https://example.com')

    drag(card, 'dragover', { dataTransfer: stranger, clientY: 10 })

    expect(view.emitted().dragOver).toBeUndefined()
  })

  it('放下时报告「谁、落到哪一格的哪一边」', async () => {
    const view = await mount(tile('b'), { dropEdge: 'before' })
    const card = view.container.querySelector('.app-rail-item')!
    const dt = dataTransfer()
    dt.setData(DRAG_TYPE, 'a')

    drag(card, 'drop', { dataTransfer: dt })

    expect(view.emitted().drop).toEqual([['a', 'b', 'before']])
  })

  it('首页那一格既拖不动也接不住', async () => {
    const view = await mount(home)
    const card = view.container.querySelector('.app-rail-item')!
    expect(card.getAttribute('draggable')).toBe('false')

    const dt = dataTransfer()
    dt.setData(DRAG_TYPE, 'a')
    drag(card, 'dragover', { dataTransfer: dt, clientY: 10 })
    drag(card, 'drop', { dataTransfer: dt })

    expect(view.emitted().dragOver).toBeUndefined()
    expect(view.emitted().drop).toBeUndefined()
  })
})
