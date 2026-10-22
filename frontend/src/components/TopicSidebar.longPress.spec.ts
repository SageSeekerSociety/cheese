// 手机上的话题列表：长按和行尾 ⋯ 都能打开同一行的操作。
//
// 长按一行，升起来的是这一行的操作；轻点是打开频道，不升起菜单；
// 面板里选「重命名」后提交，改的是被长按的那一行。
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
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale, t } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

import { screenHeader } from '@/composables/useScreenHeader'

import ScreenHeaderTitle from './common/Navigation/ScreenHeaderTitle.vue'
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
    can_manage: true,
    joined: true,
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

// Reads the declared header in its own render, after the sidebar beside it has set up.
const BarTitle = defineComponent({
  setup: () => () => h(ScreenHeaderTitle, { header: screenHeader.value, fallback: '' }),
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    // ScreenHeaderTitle stands in for the phone top bar: in page form the project row
    // is the bar's content, declared by the sidebar (useScreenHeader).
    return () =>
      h(VLayout, null, {
        default: () => [h(Sidebar, props.inner as Record<string, unknown>), h(BarTitle)],
      })
  },
})

function mount() {
  const onSelectTopic = vi.fn()
  const onRenameTopic = vi.fn()
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
        unreadMap: { a: { count: 3, new: true, messages: 3 }, b: { count: 1, new: true, messages: 1 } },
        onSelectTopic,
        onRenameTopic,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, onSelectTopic, onRenameTopic }
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

// 松手：浏览器补一下 click（长按之后的那一下由 useLongPress 吞掉）。
function release(el: HTMLElement) {
  touch(el, 'pointerup')
  el.click()
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
  it('长按升起这一行的四项操作，重命名提交的是这一行', async () => {
    const { container, baseElement, onSelectTopic, onRenameTopic } = mount()
    await Promise.resolve()
    vi.useFakeTimers()
    const row = rowOf(container, 'b')
    touch(row, 'pointerdown')
    vi.advanceTimersByTime(600)
    touch(row, 'pointerup')
    vi.useRealTimers()

    await waitFor(() => expect(sheetItems(baseElement).length).toBeGreaterThan(0))
    // 松手那一下 click 被吞掉是 useLongPress 自己的规则（happy-dom 不认捕获阶段的
    // stopImmediatePropagation，在它的用例里测），这里只看升起来的是哪一行的操作。
    expect(baseElement.querySelector('.v-bottom-sheet')?.textContent).toContain('话题b')

    expect(onSelectTopic).not.toHaveBeenCalled()

    expect(sheetItems(baseElement).map((item) => item.textContent?.trim())).toEqual([
      t('work.room.menu.newTask'),
      t('work.room.menu.allTasks'),
      t('work.room.menu.rename'),
      t('work.channel.leave'),
    ])
    const rename = sheetItems(baseElement).find((item) => item.textContent?.trim() === t('work.room.menu.rename'))
    await fireEvent.click(rename as Element)
    const input = row.querySelector('input') as HTMLInputElement
    await fireEvent.update(input, '新频道名')
    await fireEvent.keyUp(input, { key: 'Enter' })
    expect(onRenameTopic).toHaveBeenCalledWith({ id: 'b', title: '新频道名' })
    expect(onSelectTopic).not.toHaveBeenCalled()
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

  it('行尾 ⋯ 也能打开同一份操作，不触发频道选择', async () => {
    const { container, baseElement, onSelectTopic } = mount()
    await Promise.resolve()
    const row = rowOf(container, 'a')
    const more = row.querySelector(`[title="${t('work.sidebar.moreActions')}"]`) as HTMLElement
    expect(more).not.toBeNull()
    await fireEvent.click(more)
    await waitFor(() => {
      expect(sheetItems(baseElement).map((item) => item.textContent?.trim())).toEqual([
        t('work.room.menu.newTask'),
        t('work.room.menu.allTasks'),
        t('work.room.menu.rename'),
        t('work.channel.leave'),
      ])
    })
    expect(onSelectTopic).not.toHaveBeenCalled()
    expect(row.querySelector('.unread-badge')).toBeNull()
  })
})
