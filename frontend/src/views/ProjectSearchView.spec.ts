// 搜索结果页：词在地址上，打开就搜；「全部」每类列前几条，某一类比列出来的多时可以
// 点进那一栏看全；某一栏滚到底接着加载下一页，拿到的是后面的，不重复；改了词地址
// 跟着变，重新搜。
import type { ProjectSearchHits } from '@/api/projectSearch'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const searchProject = vi.hoisted(() => vi.fn())
const searchProjectCounted = vi.hoisted(() => vi.fn())
vi.mock('@/api/projectSearch', async (original) => ({
  ...(await original<object>()),
  searchProject,
  searchProjectCounted,
}))

import ProjectSearchView from './ProjectSearchView.vue'

import { t } from '@/i18n'

const Blank = defineComponent({ render: () => h('div') })

function message(n: number) {
  return {
    id: `b${n}`,
    room_id: 't1',
    room_title: '登录页改成深色',
    kind: 'message' as const,
    author: 'alice',
    author_name: 'Alice',
    author_name_source: null,
    created_at: '2026-09-01T00:00:00Z',
    task_id: null,
    snippet: `深色第 ${n} 条`,
  }
}
const ALL = Array.from({ length: 25 }, (_, i) => message(i + 1))

function page(extra: Partial<ProjectSearchHits>): ProjectSearchHits {
  return { records: [], tasks: [], library: [], ...extra }
}

// 可以手动「滚到底」：页面用 IntersectionObserver 看最后那一截露没露出来。
let reachBottom: () => void = () => {}
class FakeObserver {
  constructor(private readonly callback: IntersectionObserverCallback) {
    reachBottom = () => this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as never)
  }
  observe() {}
  disconnect() {}
}

async function mount(url: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/search', name: 'project-search', component: ProjectSearchView, props: true },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
      { path: '/:any(.*)*', component: Blank },
    ],
  })
  await router.push(url)
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, { global: { plugins: [createVuetify({ components, directives }), router, createPinia()] } })
  return { ...view, router }
}

const rows = () => screen.queryAllByRole('link').map((el) => el.textContent ?? '')

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  searchProject.mockReset()
  searchProjectCounted.mockReset()
  const slice = (limit: number, paging?: { only: string[]; offset: number }) =>
    page({ records: ALL.slice(paging?.offset ?? 0, (paging?.offset ?? 0) + limit) })
  searchProject.mockImplementation(
    async (_project: string, _q: string, limit: number, paging?: { only: string[]; offset: number }) =>
      slice(limit, paging)
  )
  searchProjectCounted.mockImplementation(
    async (_project: string, _q: string, limit: number, paging?: { only: string[]; offset: number }) => ({
      hits: slice(limit, paging),
      counts: { message: ALL.length, weekly: 0, tasks: 0, library: 0 },
    })
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('搜索结果页', () => {
  it('打开就搜地址上的词；消息比列出来的多，点「查看全部」看那一栏', async () => {
    const { router } = await mount('/projects/p1/search?q=深色')
    await waitFor(() => expect(rows().some((text) => text.includes('深色第 1 条'))).toBe(true))
    expect(rows().length).toBeLessThan(ALL.length)

    await fireEvent.click(screen.getByRole('button', { name: t('navigation.search.viewAll', { n: ALL.length }) }))
    await waitFor(() => expect(router.currentRoute.value.query.kind).toBe('messages'))
    await waitFor(() => expect(rows().length).toBe(20))
  })

  it('某一栏滚到底接着加载，后面的不重复', async () => {
    await mount('/projects/p1/search?q=深色&kind=messages')
    await waitFor(() => expect(rows().length).toBe(20))
    reachBottom()
    await waitFor(() => expect(rows().length).toBe(ALL.length))
    expect(new Set(rows()).size).toBe(ALL.length)
  })

  it('改了词，地址跟着变，按新词搜', async () => {
    const { router } = await mount('/projects/p1/search?q=深色')
    await waitFor(() => expect(searchProjectCounted).toHaveBeenCalled())
    await fireEvent.update(screen.getByRole('searchbox'), '浅色')
    await waitFor(() => expect(router.currentRoute.value.query.q).toBe('浅色'), { timeout: 2000 })
    await waitFor(() => expect(searchProjectCounted).toHaveBeenLastCalledWith('p1', '浅色', expect.anything()))
  })
})

/** 搜不到时：结果那一块就地换成错误 + 重试（docs/design-system.md §3.10），搜索框和
 *  分栏留在原地；翻下一页失败时，已经到手的那一段不能扔。
 */
describe('搜索结果读不到时', () => {
  it('结果那一块就地显示原因和重试，搜索框还在', async () => {
    searchProjectCounted.mockRejectedValueOnce(new Error('服务器错误'))
    await mount('/projects/p1/search?q=深色')

    expect(await screen.findByText(t('navigation.search.failed'))).toBeTruthy()
    expect(screen.getByText('服务器错误')).toBeTruthy()
    expect(screen.getByRole('searchbox')).toBeTruthy()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
  })

  it('点重试真的再搜一遍', async () => {
    searchProjectCounted.mockRejectedValueOnce(new Error('服务器错误'))
    await mount('/projects/p1/search?q=深色')
    await screen.findByText(t('navigation.search.failed'))
    expect(searchProjectCounted).toHaveBeenCalledTimes(1)

    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(searchProjectCounted).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(t('navigation.search.failed'))).toBeNull())
  })

  it('翻下一页失败：到手的这一段留着，错误接在它下面，重试接着翻', async () => {
    await mount('/projects/p1/search?q=深色&kind=messages')
    await waitFor(() => expect(rows().length).toBe(20))

    searchProject.mockRejectedValueOnce(new Error('翻页失败'))
    reachBottom()
    expect(await screen.findByText('翻页失败')).toBeTruthy()
    expect(rows().length).toBe(20)

    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))
    await waitFor(() => expect(rows().length).toBe(ALL.length))
  })
})
