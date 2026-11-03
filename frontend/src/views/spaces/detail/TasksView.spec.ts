/** 题目列表：还能领的在前，已截止或名额已满的收成末尾一行。 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TasksView from './TasksView.vue'

import i18n, { setLocale } from '@/i18n'
import { task } from '@/views/demo/catalogScenesSpacesDetailFixtures'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function show(more: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  return render(TasksView, {
    props: {
      scope: 'all',
      pendingOnly: false,
      hotTopics: [],
      categoryFilterOptions: [],
      spaceId: 7,
      announcements: [],
      announcementsTarget: '/',
      rowTo: () => '/',
      loadingMore: false,
      hasMore: false,
      refreshing: false,
      tasks: [task({ id: 1, name: '最短路径' })],
      total: 1,
      mineTasks: [],
      mineTotal: 0,
      mineHasMore: false,
      mineLoadingMore: false,
      closedTasks: [],
      closedTotal: 0,
      closedHasMore: false,
      closedLoadingMore: false,
      publishedLoading: false,
      publishedFailed: false,
      publishedError: null,
      publishedTasks: [],
      selectedTopics: [],
      sort: 'latestPublished',
      search: '',
      selectedCategory: null,
      ...more,
    },
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
}

describe('题目列表的末尾', () => {
  it('没有收起的题目时，列表末尾写「没有更多了」', () => {
    const view = show()

    expect(view.getAllByText('没有更多了')).toHaveLength(1)
    expect(view.queryByTestId('tasks-closed')).toBeNull()
  })

  it('有收起的题目时，开放的那一段不写「没有更多了」，点开后只在最后写一次', async () => {
    const view = show({
      closedTasks: [task({ id: 2, name: '上学期的期末项目', deadline: 1751212800000 })],
      closedTotal: 1,
    })

    expect(view.queryByText('没有更多了')).toBeNull()
    await fireEvent.click(view.getByText('已截止或名额已满（1）'))
    await view.rerender({ closedOpen: true })

    view.getByText('上学期的期末项目')
    expect(view.getAllByText('没有更多了')).toHaveLength(1)
  })
})

describe('「我参与的」那一组', () => {
  it('在列表最上面，带着条数，不收起', () => {
    const view = show({
      mineTasks: [task({ id: 3, name: '我在做的题', joined: true, submittable: true })],
      mineTotal: 1,
    })

    view.getByText('我参与的（1）')
    view.getByText('我在做的题')
    view.getByText('最短路径')
  })

  it('我手上一道都没有：不画这一组', () => {
    const view = show()

    expect(view.queryByText(/我参与的（/)).toBeNull()
  })
})
