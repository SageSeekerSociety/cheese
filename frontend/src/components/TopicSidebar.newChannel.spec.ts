// 「新建频道」在侧栏上的入口：有两个以上频道时频道那一组标题右边常驻一颗 ＋，只有一个时
// ＋ 挂在「对话」那一行上，点它都是就地在侧栏弹对话框；
// 只有「综合」时不另外催人建频道。外部成员没有 ＋。
//
// 这一层不认识 store，所以它只发一个 `new-channel` 事件——对话框开在哪、东西建到哪，
// 是外面的事（`ProjectSidebar.vue`）。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, flags: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind: parentId === null ? 'root' : 'channel',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
    joined: true,
    ...flags,
  } as Topic
}

/** 只有项目自带的「综合」：再没有别的频道。 */
const onlyRoot: Topic[] = [topic('root', null)]
/** 还有我一个加入了的频道。 */
const withChannel: Topic[] = [topic('root', null), topic('mine', 'root')]

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

function mount(inner: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  const onNewChannel = vi.fn()
  const utils = render(Host, {
    props: {
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: withChannel,
        selectedTopicId: null,
        loadingTopics: false,
        canCreate: true,
        onNewChannel,
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, onNewChannel }
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

describe('侧栏「新建频道」入口', () => {
  it('频道标题右边有一颗 ＋，点它请求弹对话框', async () => {
    const { container, onNewChannel } = mount()
    const add = container.querySelector('[data-testid="new-channel-entry"]')
    expect(add).not.toBeNull()
    await fireEvent.click(add!)
    expect(onNewChannel).toHaveBeenCalled()
  })

  // 一个简单的项目只有「综合」就够了：不因为没有别的频道就催人去建，入口只有那一颗 ＋。
  it('只有「综合」时不催人建频道，入口挂在「对话」那一行上', async () => {
    const { container, getAllByRole, onNewChannel } = mount({ topics: onlyRoot })
    const add = container.querySelector('[data-testid="new-channel-entry"]')
    expect(add).not.toBeNull()
    expect(getAllByRole('button').filter((b) => b.textContent?.includes('新建频道'))).toHaveLength(0)
    await fireEvent.click(add!)
    expect(onNewChannel).toHaveBeenCalled()
  })

  it('外部成员：不给 ＋', () => {
    const { container } = mount({ canCreate: false, topics: onlyRoot })
    expect(container.querySelector('[data-testid="new-channel-entry"]')).toBeNull()
  })
})

describe('只有一个频道的项目', () => {
  // 项目里我看得到的频道只有一个：它就是这个项目的对话，不再画「频道」这一组和「全部频道」。
  it('那一行写「对话」，没有「频道」这一组的标题和「全部频道」', () => {
    const { queryByText, getByText } = mount({ topics: onlyRoot })
    expect(getByText('对话')).toBeTruthy()
    expect(queryByText('综合')).toBeNull()
    expect(queryByText('频道')).toBeNull()
    expect(queryByText('全部频道')).toBeNull()
  })

  it('手机上的整页列表照常分组', () => {
    const { getByText, queryByText } = mount({ topics: onlyRoot, page: true })
    expect(getByText('综合')).toBeTruthy()
    expect(getByText('频道')).toBeTruthy()
    expect(queryByText('对话')).toBeNull()
  })

  // 没加入的频道我也看得到，它也算一个频道：这时照常是分组的样子。
  it('还有一个我没加入的频道时，照常分组写频道名', () => {
    const { getByText, queryByText } = mount({
      topics: [topic('root', null), topic('other', 'root', { joined: false })],
    })
    expect(getByText('综合')).toBeTruthy()
    expect(getByText('频道')).toBeTruthy()
    expect(getByText('全部频道')).toBeTruthy()
    expect(queryByText('对话')).toBeNull()
  })

  it('建出第二个频道，不刷新页面就换成分组的样子', async () => {
    const { getByText, queryByText, rerender } = mount({ topics: onlyRoot })
    expect(getByText('对话')).toBeTruthy()
    await rerender({
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: withChannel,
        selectedTopicId: null,
        loadingTopics: false,
        canCreate: true,
      },
    })
    expect(getByText('综合')).toBeTruthy()
    expect(getByText('全部频道')).toBeTruthy()
    expect(queryByText('对话')).toBeNull()
  })
})
