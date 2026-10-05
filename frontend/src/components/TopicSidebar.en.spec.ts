/** 界面切到英文时，话题侧栏不能再漏出中文。
 *
 * 用英文界面渲染侧栏的几种样子（没选项目、项目里还没有话题、有别人的话题），断言
 * 渲染结果（含 title / aria-label）里一个汉字都没有。夹具数据用英文写：这里要抓的是
 * 写死在界面里的字，不是用户给的内容。
 */
import type { Component } from 'vue'
import type { Project, Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/
const Sidebar = TopicSidebar as unknown as Component
const Blank = defineComponent({ setup: () => () => h('div') })

const base = {
  project_id: 'p1',
  created_by: 'u',
  created_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
}
const root = { ...base, id: 'root', parent_id: null, title: 'General', kind: 'root', status: 'active' } as Topic
const theirs = {
  ...base,
  id: 't1',
  parent_id: 'root',
  title: 'Launch plan',
  kind: 'channel',
  status: 'active',
  i_participate: false,
} as Topic

const project = { id: 'p1', name: 'Course', created_at: '2026-08-10T00:00:00Z' } as unknown as Project

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/:pathMatch(.*)*', name: 'catch-all', component: Blank }],
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown>) {
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  return render(Host, {
    props: {
      inner: {
        projects: [project],
        selectedProjectId: 'p1',
        topics: [root],
        selectedTopicId: null,
        loadingTopics: false,
        ...inner,
      },
    },
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
}

function chineseIn(root: Element): string[] {
  const attrs = Array.from(root.querySelectorAll('[title],[aria-label],[placeholder]')).flatMap((el) =>
    ['title', 'aria-label', 'placeholder'].map((a) => el.getAttribute(a) ?? '')
  )
  return [root.textContent ?? '', ...attrs].filter((s) => CJK.test(s))
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

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  setLocale('en')
})
afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

describe('the topic sidebar in English', () => {
  it('with no project chosen', () => {
    const { baseElement, getByText } = mount({ projects: [], selectedProjectId: null, topics: [] })
    getByText('Choose a project first')
    expect(chineseIn(baseElement)).toEqual([])
  })

  it('in a project with no topics yet', () => {
    const { baseElement, getByText, getByLabelText } = mount({})
    getByText('No channels yet')
    getByLabelText('New channel')
    expect(chineseIn(baseElement)).toEqual([])
  })

  it('while a topic is being created', () => {
    const { baseElement, getByLabelText } = mount({ creatingTopic: true })
    getByLabelText('Creating channel')
    expect(chineseIn(baseElement)).toEqual([])
  })

  it('when the only topics belong to other people', () => {
    const { baseElement, getByText } = mount({ topics: [root, theirs] })
    getByText('General')
    expect(chineseIn(baseElement)).toEqual([])
  })
})
