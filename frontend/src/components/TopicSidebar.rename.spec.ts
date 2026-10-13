// 侧栏里就地改名：行尾那颗 ⋯ → 重命名，标题原地变成输入框，回车/失焦提交。
//
// 提交出去的是 `rename-topic`，落盘的活儿归父级（ProjectSidebar → store）——
// 这一份只钉「按了什么、发了什么」：改过才发、没改不发、空的不发、Esc 不发。
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
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, kind = 'topic'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
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

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(over: Partial<Topic> = {}) {
  const onRenameTopic = vi.fn()
  const vuetify = createVuetify({ components, directives })
  // 覆盖只落在频道上，「综合」保持原样（它不能改名，见下面那条）。
  const rows = topics.map((row) => (row.kind === 'root' ? row : { ...row, ...over }))
  const utils = render(Host, {
    props: {
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: rows,
        selectedTopicId: null,
        loadingTopics: false,
        onRenameTopic,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, onRenameTopic }
}

/** 点开这一行的 ⋯，等菜单出来，返回菜单里每一行的字。 */
async function menuOn(container: Element, baseElement: Element, title: string): Promise<string[]> {
  const row = rowFor(container, title)
  await fireEvent.click(row.querySelector('[title="更多操作"]') as HTMLElement)
  return waitFor(() => {
    const found = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map((el) =>
      el.textContent?.trim()
    )
    if (!found.length) throw new Error('菜单没出来')
    return found
  })
}

function rowFor(container: Element, title: string): HTMLElement {
  const row = Array.from(container.querySelectorAll('.topic-row')).find(
    (el) => el.querySelector('.topic-title .text-truncate')?.textContent?.trim() === title
  )
  if (!row) throw new Error(`没有找到话题行: ${title}`)
  return row as HTMLElement
}

/** 点开这一行的 ⋯，选中「重命名」，返回那一行和新出现的输入框。 */
async function startRenameOn(
  container: Element,
  baseElement: Element,
  title: string
): Promise<{ row: HTMLElement; input: HTMLInputElement }> {
  const row = rowFor(container, title)
  await fireEvent.click(row.querySelector('[title="更多操作"]') as HTMLElement)
  const item = await waitFor(() => {
    const found = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find(
      (el) => el.textContent?.trim() === t('work.room.menu.rename')
    )
    if (!found) throw new Error('菜单里没有「重命名」')
    return found as HTMLElement
  })
  await fireEvent.click(item)
  await waitFor(() => expect(row.querySelector('.rename-field')).not.toBeNull())
  return { row, input: row.querySelector('.rename-field input') as HTMLInputElement }
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
  // 打开一个 overlay 就要这些：Vuetify 的定位直接读它们，happy-dom 没有。
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => localStorage.clear())

describe('就地改名', () => {
  it('输入框里先是现在的名字', async () => {
    const { container, baseElement } = mount()
    const { row, input } = await startRenameOn(container, baseElement, 'a')
    expect(input.value).toBe('a')
    // 同一时刻只有这一行在改：别的行照旧是标题。
    expect(rowFor(container, 'b').querySelector('.rename-field')).toBeNull()
    expect(row.querySelector('.topic-title .text-truncate')).toBeNull()
  })

  it('回车提交：发出去的是新名字，输入框收回去', async () => {
    const { container, baseElement, onRenameTopic } = mount()
    const { row, input } = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(input, '第 3 题：为什么天空是蓝的')
    await fireEvent.keyUp(input, { key: 'Enter' })

    expect(onRenameTopic).toHaveBeenCalledWith({ id: 'a', title: '第 3 题：为什么天空是蓝的' })
    expect(row.querySelector('.rename-field')).toBeNull()
    expect(row.querySelector('.topic-title .text-truncate')?.textContent?.trim()).toBe('a')
  })

  it('失焦也算提交', async () => {
    const { container, baseElement, onRenameTopic } = mount()
    const { row, input } = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(input, '改过的名字')
    await fireEvent.blur(input)

    expect(onRenameTopic).toHaveBeenCalledWith({ id: 'a', title: '改过的名字' })
    expect(row.querySelector('.rename-field')).toBeNull()
  })

  it('Esc 什么都不发，名字退回原样', async () => {
    const { container, baseElement, onRenameTopic } = mount()
    const { row, input } = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(input, '不要这个名字')
    await fireEvent.keyUp(input, { key: 'Escape' })

    expect(onRenameTopic).not.toHaveBeenCalled()
    expect(row.querySelector('.rename-field')).toBeNull()
    expect(row.querySelector('.topic-title .text-truncate')?.textContent?.trim()).toBe('a')
  })

  it('没改动、空名字、只有空白：都不发', async () => {
    const { container, baseElement, onRenameTopic } = mount()
    const first = await startRenameOn(container, baseElement, 'a')
    await fireEvent.keyUp(first.input, { key: 'Enter' })
    expect(onRenameTopic).not.toHaveBeenCalled()

    const again = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(again.input, '   ')
    await fireEvent.keyUp(again.input, { key: 'Enter' })
    expect(onRenameTopic).not.toHaveBeenCalled()
  })

  it('两头的空格去掉再发', async () => {
    const { container, baseElement, onRenameTopic } = mount()
    const { input } = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(input, '  名字在中间  ')
    await fireEvent.keyUp(input, { key: 'Enter' })

    expect(onRenameTopic).toHaveBeenCalledWith({ id: 'a', title: '名字在中间' })
  })

  // 频道叫什么名字是这个频道自己的事，不归建它的人：不是管理者照样能改，后端同样只认
  // 「你能进这个频道」（`topics_title.py`）。归档仍然只给管理者。
  it('不是管理者也能改名', async () => {
    const { container, baseElement, onRenameTopic } = mount({ can_manage: false })
    const { input } = await startRenameOn(container, baseElement, 'a')
    await fireEvent.update(input, '新名字')
    await fireEvent.keyUp(input, { key: 'Enter' })

    expect(onRenameTopic).toHaveBeenCalledWith({ id: 'a', title: '新名字' })
  })

  it('不是管理者：菜单里有「重命名」，没有「归档」', async () => {
    const { container, baseElement } = mount({ can_manage: false })
    const labels = await menuOn(container, baseElement, 'a')
    expect(labels).toContain(t('work.room.menu.rename'))
    expect(labels).not.toContain(t('work.room.menu.archive'))
  })

  it('管理者：两样都有', async () => {
    const { container, baseElement } = mount()
    const labels = await menuOn(container, baseElement, 'a')
    expect(labels).toContain(t('work.room.menu.rename'))
    expect(labels).toContain(t('work.room.menu.archive'))
  })
})
