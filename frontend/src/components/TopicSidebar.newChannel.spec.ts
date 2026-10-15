// 「新建频道」在侧栏上的入口：频道那一组标题右边常驻一颗 ＋，点它就地在侧栏弹对话框；
// 项目里还没有别的频道时，这一组下面把话说出来并给一颗主按钮。外部成员两样都没有。
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

  it('一个别的频道都还没有：把话说出来，并给一颗主按钮', async () => {
    const { container, onNewChannel } = mount({ topics: onlyRoot })
    const empty = container.querySelector('[data-testid="no-other-channels"]')
    expect(empty).not.toBeNull()
    expect(empty!.textContent).toContain('还没有别的频道')
    await fireEvent.click(empty!.querySelector('button')!)
    expect(onNewChannel).toHaveBeenCalled()
  })

  it('已经有别的频道：不说「还没有别的频道」', () => {
    const { container } = mount()
    expect(container.querySelector('[data-testid="no-other-channels"]')).toBeNull()
  })

  it('外部成员：＋ 和主按钮都不给', () => {
    const { container } = mount({ canCreate: false, topics: onlyRoot })
    expect(container.querySelector('[data-testid="new-channel-entry"]')).toBeNull()
    expect(container.querySelector('[data-testid="no-other-channels"]')).toBeNull()
  })
})
