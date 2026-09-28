// 手机上的话题列表：一行的操作靠长按打开（行尾不再常驻一颗 ⋯ 盖住未读数）。
//
// 人在读代码之前就说得出的几条：长按一行，升起来的是**这一行**的操作；轻点一行是
// 打开它，不升起任何东西；面板里选「归档」归档的是被长按的那一行。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, kind = 'topic'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: `话题${id}`,
    kind,
    status: 'active',
    can_archive: true,
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic
}

const topics: Topic[] = [topic('root', null, 'root'), topic('a', 'root'), topic('b', 'root')]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount() {
  const onSelectTopic = vi.fn()
  const onArchiveTopic = vi.fn()
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  const vuetify = createVuetify({ components, directives })
  const utils = render(Host, {
    props: {
      inner: {
        page: true,
        projects: [{ id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        // 两行都算「我参与的」，平铺在上面那一组里。
        unreadMap: { a: 3, b: 1 },
        onSelectTopic,
        onArchiveTopic,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, onSelectTopic, onArchiveTopic }
}

function rowOf(container: Element, id: string): HTMLElement {
  const row = container.querySelector(`[data-room-id="${id}"]`) as HTMLElement | null
  if (!row) throw new Error(`没有 ${id} 这一行`)
  return row
}

function touch(el: Element, type: string) {
  el.dispatchEvent(
    new PointerEvent(type, {
      bubbles: true,
      cancelable: true,
      pointerId: 1,
      pointerType: 'touch',
      clientX: 5,
      clientY: 5,
    })
  )
}

// 松手：浏览器在 touchend 没被取消时才补一下 click（取消了就不补）。
function release(el: HTMLElement) {
  touch(el, 'pointerup')
  const end = new Event('touchend', { bubbles: true, cancelable: true })
  el.dispatchEvent(end)
  if (!end.defaultPrevented) el.click()
}

function sheetItems(baseElement: Element): HTMLElement[] {
  return Array.from(baseElement.querySelectorAll('.v-bottom-sheet [role="menuitem"]'))
}

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
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
  ;(window as unknown as { innerWidth: number }).innerWidth = 390
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('手机话题列表：长按一行', () => {
  it('长按升起这一行的操作，选「归档」归档的是这一行，不会顺手把它打开', async () => {
    const { container, baseElement, onSelectTopic, onArchiveTopic } = mount()
    await Promise.resolve()
    vi.useFakeTimers()
    const row = rowOf(container, 'b')
    touch(row, 'pointerdown')
    vi.advanceTimersByTime(600)
    release(row)
    vi.useRealTimers()

    await waitFor(() => expect(sheetItems(baseElement).length).toBeGreaterThan(0))
    expect(baseElement.querySelector('.v-bottom-sheet')?.textContent).toContain('话题b')

    expect(onSelectTopic).not.toHaveBeenCalled()

    // 面板里的归档：mdi 图标名说的是哪一项，文案不进断言。
    const archive = sheetItems(baseElement).find((el) => el.querySelector('.mdi-archive-arrow-down-outline'))
    await fireEvent.click(archive as Element)
    expect(onArchiveTopic).toHaveBeenCalledWith('b')
  })

  it('轻点一行是打开它，不升起面板', async () => {
    const { container, baseElement, onSelectTopic } = mount()
    await Promise.resolve()
    vi.useFakeTimers()
    const row = rowOf(container, 'a')
    touch(row, 'pointerdown')
    vi.advanceTimersByTime(120)
    release(row)
    vi.advanceTimersByTime(1000)
    vi.useRealTimers()

    expect(onSelectTopic).toHaveBeenCalledWith('a')
    expect(sheetItems(baseElement)).toHaveLength(0)
  })

  it('行尾不常驻 ⋯：未读数没有东西盖着', async () => {
    const { container } = mount()
    await Promise.resolve()
    expect(rowOf(container, 'a').querySelector('[title="更多操作"]')).toBeNull()
  })
})
