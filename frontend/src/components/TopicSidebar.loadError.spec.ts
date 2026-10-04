// 侧栏话题列表**整块**没读到时画的是什么。
//
// 读失败不是「暂无话题」：那说的是「这里本来就没有」。失败要留在它读的那块地方，
// 摆出服务端那句原话和一条重试（§3.10）。这一份钉的是这个区别，以及重试的出口。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: { render: () => h('div') } }],
})

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
        topics: [] as Topic[],
        selectedTopicId: null,
        loadingTopics: false,
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

describe('话题列表读失败的侧栏', () => {
  it('整块换成失败块：服务端原话 + 重试，不退回「暂无话题」', async () => {
    const { container, queryByText } = mount({ topicsError: 'HTTP 503 for /topics' })
    expect(await screen.findByText(t('shell.workspaceErrors.loadTopics'))).toBeTruthy()
    expect(queryByText('HTTP 503 for /topics')).toBeTruthy()
    expect(queryByText(t('work.sidebar.empty')), '失败不能显示成「暂无话题」').toBeNull()
    // 骨架和失败块不会同时在场。
    expect(container.querySelector('[role="status"][aria-busy="true"]')).toBeNull()
  })

  it('点重试发出 retry-topics', async () => {
    const onRetryTopics = vi.fn()
    mount({ topicsError: 'HTTP 503 for /topics', onRetryTopics })
    await fireEvent.click(await screen.findByRole('button', { name: t('global.loadError.retry') }))
    expect(onRetryTopics).toHaveBeenCalledTimes(1)
  })

  it('读成功时不画失败块', async () => {
    const { queryByText } = mount({ topicsError: null })
    expect(queryByText(t('shell.workspaceErrors.loadTopics'))).toBeNull()
  })
})
