import type { RoomTask } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ list: vi.fn(), page: vi.fn() }))
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  listProjectTasks: api.list,
  pageProjectTasks: api.page,
}))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

import ProjectTasks from './ProjectTasks.vue'

import { setLocale, t } from '@/i18n'
import { queryClient } from '@/query/client'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
const accepted: RoomTask = {
  id: 'accepted',
  project_id: 'p1',
  room_id: 'front',
  title: '已采纳交付的任务',
  status: 'closed',
  owner_handle: 'alice',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  accepted_at: '2026-10-01T00:00:00Z',
  presentation: { column: 'done', phrase: 'accepted' },
}
const other: RoomTask = { ...accepted, id: 'other', title: '未采纳交付的任务', accepted_at: null }

beforeEach(() => {
  queryClient.clear()
  api.list.mockReset().mockResolvedValue({ data: [], rev: '1' })
  api.page.mockReset().mockImplementation(async (_project, opts) => {
    const data = opts.accepted ? [accepted] : [accepted, other]
    return { data, has_more: false, next: null, counts: { all: data.length, mine: data.length, helping: 0, others: 0 } }
  })
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
  setActivePinia(createPinia())
})
afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.unstubAllGlobals()
})

async function mount(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/tasks', name: 'workspace-tasks', component: ProjectTasks },
      {
        path: '/projects/:projectId/topics/:topicId/tasks/:taskId',
        name: 'workspace-task',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(path)
  await router.isReady()
  const view = render(ProjectTasks, {
    props: { projectId: 'p1' },
    global: { plugins: [router, createVuetify({ components, directives })] },
  })
  return { view, router }
}

it('从地址恢复三组筛选与频道，离开任务后返回仍保留', async () => {
  const { view, router } = await mount('/projects/p1/tasks?status=closed&whose=mine&delivery=accepted&channel=front')
  const row = await view.findByRole('button', { name: /已采纳交付的任务/ })
  expect(
    view.getByRole('button', { name: t('work.channelTasks.closed'), exact: true }).getAttribute('aria-pressed')
  ).toBe('true')
  expect(view.getByRole('button', { name: t('taskList.acceptedDelivery') }).getAttribute('aria-pressed')).toBe('true')
  expect(view.getByRole('button', { name: /^我负责的 1$/ }).getAttribute('aria-pressed')).toBe('true')
  expect(api.page).toHaveBeenCalledWith(
    'p1',
    expect.objectContaining({ status: 'closed', whose: 'mine', accepted: true, channel: 'front' })
  )
  await fireEvent.click(row)
  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-task'))
  router.back()
  await waitFor(() =>
    expect(router.currentRoute.value.query).toEqual({
      status: 'closed',
      whose: 'mine',
      delivery: 'accepted',
      channel: 'front',
    })
  )
  expect(await view.findByRole('button', { name: /已采纳交付的任务/ })).toBeTruthy()
})

it('交付筛选使用独立的分页结果与总数，不串到未筛选的缓存', async () => {
  const { view, router } = await mount('/projects/p1/tasks?status=closed')
  expect(await view.findByRole('button', { name: /未采纳交付的任务/ })).toBeTruthy()
  await fireEvent.click(view.getByRole('button', { name: t('taskList.acceptedDelivery') }))
  await waitFor(() => expect(router.currentRoute.value.query.delivery).toBe('accepted'))
  await waitFor(() => expect(view.queryByRole('button', { name: /未采纳交付的任务/ })).toBeNull())
  expect(await view.findByRole('button', { name: /^全部 1$/ })).toBeTruthy()
  await fireEvent.click(view.getByRole('button', { name: t('taskList.anyDelivery') }))
  expect(await view.findByRole('button', { name: /未采纳交付的任务/ })).toBeTruthy()
  expect(view.getByRole('button', { name: /^全部 2$/ })).toBeTruthy()
  await waitFor(() => expect(router.currentRoute.value.query).toEqual({ status: 'closed' }))
})

it('无效和重复的地址参数回到默认筛选，不产生无效请求', async () => {
  const { view } = await mount('/projects/p1/tasks?status=wrong&delivery=wrong&whose=mine&whose=mine')
  await waitFor(() => expect(view.getByRole('button', { name: /^全部 0$/ }).getAttribute('aria-pressed')).toBe('true'))
  expect(view.getByRole('button', { name: t('taskList.open'), exact: true }).getAttribute('aria-pressed')).toBe('true')
  expect(view.getByRole('button', { name: t('taskList.anyDelivery') }).getAttribute('aria-pressed')).toBe('true')
  expect(api.page).not.toHaveBeenCalled()
})
