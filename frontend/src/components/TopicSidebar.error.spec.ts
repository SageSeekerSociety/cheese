// 话题清单读失败时，侧栏画的是什么。
//
// 从前这条失败只经 store.error 弹一条几秒的 toast：几秒之后，这一块和「暂无话题」
// 长得一模一样，人再也分不出是「坏了」还是「本来就没有」。现在它要**留在原地**：
// 一句「加载话题失败」＋服务端那句原因＋一条重试的路（docs/design-system.md §3.10）。
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

const topics: Topic[] = [
  { id: 'root', project_id: 'p1', parent_id: null, title: 'root', kind: 'root', status: 'active' } as Topic,
]

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: { render: () => h('div') } }],
})

// v-navigation-drawer 必须活在一个 v-layout 里（同 TopicSidebar.rail.spec）。
const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown> = {}) {
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  const vuetify = createVuetify({ components, directives })
  return render(Host, {
    props: {
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: [],
        selectedTopicId: null,
        loadingTopics: false,
        error: null,
        privateActive: false,
        members: [],
        meHandle: 'me',
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

beforeAll(() => {
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

describe('话题清单读失败时的侧栏', () => {
  it('就地画出「加载话题失败」＋服务端原因，带一颗重试', () => {
    const { container } = mount({ error: '服务器错误' })
    const alert = container.querySelector('.base-load-error[role="alert"]')
    expect(alert, '读失败应当就地画出错误块').not.toBeNull()
    expect(alert!.textContent).toContain('加载频道失败')
    expect(alert!.textContent).toContain('服务器错误')
    expect(alert!.querySelector('button')?.textContent?.trim()).toBe('重试')
    expect(container.querySelector('.skel'), '失败态不该还画骨架').toBeNull()
  })

  it('还有「加载中」标记时，错误优先——不画骨架', () => {
    const { container } = mount({ loadingTopics: true, error: '服务器错误' })
    expect(container.querySelector('.base-load-error')).not.toBeNull()
    expect(container.querySelector('[role="status"][aria-busy="true"]'), '错误在，骨架不该也在').toBeNull()
  })

  it('点重试，把「再读一次」交给拥有这份数据的父级', async () => {
    const onRetry = vi.fn()
    const { container } = mount({ error: '服务器错误', onRetry })
    await fireEvent.click(container.querySelector('.base-load-error button')!)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('没失败时不画错误块，读到的话题照常上屏', () => {
    const { container } = mount({ topics })
    expect(container.querySelector('.base-load-error')).toBeNull()
  })
})
