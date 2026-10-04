// 搜索结果页：词在地址上，打开就搜；「全部」每类列前几条，某一类比列出来的多时可以
// 点进那一栏看全；某一栏滚到底接着加载下一页，拿到的是后面的，不重复；改了词地址
// 跟着变，重新搜。
import type { ProjectSearchHits } from '@/api'

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
vi.mock('@/api', async (original) => ({ ...(await original<object>()), searchProject, searchProjectCounted }))

import ProjectSearchView from './ProjectSearchView.vue'

import { ApiError } from '@/api'
import { t } from '@/i18n'

const Blank = defineComponent({ render: () => h('div') })

function message(n: number) {
  return {
    id: `b${n}`,
    room_id: 't1',
    room_title: '登录页改成深色',
    kind: 'message' as const,
    author: 'alice',
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

// 读失败留在结果那块地方：整块换成失败块（§3.10），给服务端原话和一条重试；绝不退
// 回「暂无结果」。401/403 说没权限、不给重试。
describe('搜索结果读失败', () => {
  it('整块换成失败块：服务端原话 + 重试，不显示「暂无结果」', async () => {
    searchProjectCounted.mockRejectedValue(new Error('HTTP 500 for /search'))
    await mount('/projects/p1/search?q=深色')
    expect(await screen.findByText(t('navigation.search.failed'))).toBeTruthy()
    expect(screen.getByText('HTTP 500 for /search')).toBeTruthy()
    expect(screen.queryByText(t('navigation.palette.empty')), '失败不能显示成「暂无结果」').toBeNull()
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
  })

  it('点重试，重新读一次：失败块收掉', async () => {
    searchProjectCounted.mockRejectedValueOnce(new Error('boom'))
    await mount('/projects/p1/search?q=深色')
    const retry = await screen.findByRole('button', { name: t('global.loadError.retry') })
    searchProjectCounted.mockImplementation(
      async (_project: string, _q: string, limit: number, paging?: { only: string[]; offset: number }) => ({
        hits: page({ records: ALL.slice(paging?.offset ?? 0, (paging?.offset ?? 0) + limit) }),
        counts: { message: ALL.length, weekly: 0, tasks: 0, library: 0 },
      })
    )
    await fireEvent.click(retry)
    await waitFor(() => expect(screen.queryByText(t('navigation.search.failed'))).toBeNull())
  })

  it('401/403：说没权限，不给重试', async () => {
    searchProjectCounted.mockRejectedValue(new ApiError(403, 'forbidden'))
    await mount('/projects/p1/search?q=深色')
    expect(await screen.findByText(t('global.loadError.forbidden'))).toBeTruthy()
    expect(screen.queryByRole('button', { name: t('global.loadError.retry') }), '没权限不该给重试').toBeNull()
  })
})
