// 侧栏任务行里的改名：行尾那颗 ⋯ → 重命名，标题原地变成输入框，回车/失焦提交。
//
// 提交出去的是 `rename-task`，落盘的活儿归父级（ProjectSidebar → store）——这一份
// 只钉「按了什么、发了什么」：改过才发、没改不发、空的不发、Esc 不发。右键弹的是
// 同一份操作；借来凑数的行（filling_in）没有这颗 ⋯。
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

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  // 「我是谁」读的是登录后落下的那一份账号（`myHandle()`）。任务行菜单按它判能不能改名。
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
})

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

function railTask(id: string, room: string, title: string, over: Record<string, unknown> = {}) {
  return {
    id,
    project_id: 'p1',
    room_id: room,
    title,
    title_source: 'person',
    status: 'open',
    presentation: { column: 'building', phrase: 'started' },
    awaits_me: false,
    unseen_delivery: false,
    incomplete_since: null,
    owner_handle: 'me',
    contributor_handles: [],
    ...over,
  }
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

function mount(more: Record<string, unknown> = {}) {
  const onRenameTask = vi.fn()
  const vuetify = createVuetify({ components, directives })
  const utils = render(Host, {
    props: {
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        roomTasks: { a: [railTask('t1', 'a', '任务甲')] },
        roomTaskTotals: { a: 1 },
        onRenameTask,
        ...more,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, onRenameTask }
}

function taskRowFor(container: Element, title: string): HTMLElement {
  const row = Array.from(container.querySelectorAll('.rail-task-wrap')).find(
    (el) => el.querySelector('.rail-task__title')?.textContent?.trim() === title
  )
  if (!row) throw new Error(`没有找到任务行: ${title}`)
  return row as HTMLElement
}

/** 点开这一行的 ⋯，选中「重命名」，返回那一行和输入框。 */
async function startRenameOn(
  container: Element,
  baseElement: Element,
  title: string
): Promise<{ row: HTMLElement; input: HTMLInputElement }> {
  const row = taskRowFor(container, title)
  await fireEvent.click(row.querySelector('[title="更多操作"]') as HTMLElement)
  const item = await waitFor(() => {
    const found = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find(
      (el) => el.textContent?.trim() === t('work.task.rename')
    )
    if (!found) throw new Error('菜单里没有「重命名」')
    return found as HTMLElement
  })
  await fireEvent.click(item)
  await waitFor(() => expect(row.querySelector('[data-testid="task-rename-field"]')).not.toBeNull())
  return { row, input: row.querySelector('[data-testid="task-rename-field"] input') as HTMLInputElement }
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

describe('任务行改名', () => {
  it('输入框里先是现在的名字', async () => {
    const { container, baseElement } = mount()
    const { row, input } = await startRenameOn(container, baseElement, '任务甲')
    expect(input.value).toBe('任务甲')
    expect(row.querySelector('.rail-task__title')).toBeNull()
  })

  it('回车提交：发出去的是这件任务、新名字和它所在的频道', async () => {
    const { container, baseElement, onRenameTask } = mount()
    const { row, input } = await startRenameOn(container, baseElement, '任务甲')
    await fireEvent.update(input, '任务甲改')
    await fireEvent.keyUp(input, { key: 'Enter' })

    expect(onRenameTask).toHaveBeenCalledWith({ id: 't1', roomId: 'a', title: '任务甲改' })
    expect(row.querySelector('[data-testid="task-rename-field"]')).toBeNull()
    expect(row.querySelector('.rail-task__title')?.textContent?.trim()).toBe('任务甲')
  })

  it('失焦也算提交', async () => {
    const { container, baseElement, onRenameTask } = mount()
    const { input } = await startRenameOn(container, baseElement, '任务甲')
    await fireEvent.update(input, '改过的名字')
    await fireEvent.blur(input)

    expect(onRenameTask).toHaveBeenCalledWith({ id: 't1', roomId: 'a', title: '改过的名字' })
  })

  it('Esc 什么都不发', async () => {
    const { container, baseElement, onRenameTask } = mount()
    const { input } = await startRenameOn(container, baseElement, '任务甲')
    await fireEvent.update(input, '不要这个名字')
    await fireEvent.keyUp(input, { key: 'Escape' })

    expect(onRenameTask).not.toHaveBeenCalled()
  })

  it('没改动、只有空白：都不发', async () => {
    const { container, baseElement, onRenameTask } = mount()
    const first = await startRenameOn(container, baseElement, '任务甲')
    await fireEvent.keyUp(first.input, { key: 'Enter' })
    expect(onRenameTask).not.toHaveBeenCalled()

    // 收回去还是原来的标题；再开一次，这次只打空白。
    const second = await startRenameOn(container, baseElement, '任务甲')
    await fireEvent.update(second.input, '   ')
    await fireEvent.keyUp(second.input, { key: 'Enter' })
    expect(onRenameTask).not.toHaveBeenCalled()
  })

  it('右键一行弹出同一份操作，不打开这件任务', async () => {
    const { container, baseElement } = mount()
    const row = taskRowFor(container, '任务甲')
    row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 60 }))
    await waitFor(() => {
      const items = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map((el) =>
        el.textContent?.trim()
      )
      expect(items).toContain(t('work.task.rename'))
    })
  })

  it('别人的任务：菜单里只有复制链接，没有重命名', async () => {
    const { container, baseElement } = mount({
      roomTasks: { a: [railTask('t9', 'a', '别人的活', { filling_in: true, owner_handle: 'someone' })] },
      roomTaskTotals: { a: 1 },
    })
    const row = taskRowFor(container, '别人的活')
    await fireEvent.click(row.querySelector('[title="更多操作"]') as HTMLElement)
    const items = await waitFor(() => {
      const found = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map((el) =>
        el.textContent?.trim()
      )
      if (!found.length) throw new Error('菜单没出来')
      return found
    })
    expect(items).toContain(t('work.room.menu.copyLink'))
    expect(items).not.toContain(t('work.task.rename'))
  })
})
