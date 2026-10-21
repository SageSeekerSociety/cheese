// 侧栏的精简菜单只限制入口，不给频道扩大原有权限；回调仍对准被操作的频道。
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ createRoomTask: vi.fn() }))
vi.mock('@/api/tasks', async (original) => ({ ...(await original<object>()), createRoomTask: api.createRoomTask }))
vi.mock('@/lib/routePrefetch', () => ({ prefetchOnHover: vi.fn(), cancelPrefetch: vi.fn() }))

import { useTopicRailRoutes } from './useTopicRailRoutes'

import { topicActions } from '@/commands/topicActions'
import { useWorkspaceStore } from '@/stores/workspace'

function channel(over: Partial<Topic> = {}): Topic {
  return {
    id: 'channel-a',
    project_id: 'project-a',
    title: 'Channel A',
    kind: 'channel',
    status: 'active',
    joined: true,
    can_manage: false,
    ...over,
  } as Topic
}

function mount() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { render: () => null } },
      { path: '/projects/:projectId/tasks', name: 'project-tasks', component: { render: () => null } },
      {
        path: '/projects/:projectId/topics/:topicId/tasks/:taskId',
        name: 'workspace-task',
        component: { render: () => null },
      },
    ],
  })
  const rename = vi.fn()
  const renameTask = vi.fn()
  let routes!: ReturnType<typeof useTopicRailRoutes>
  render(
    defineComponent({
      setup() {
        routes = useTopicRailRoutes(() => 'project-a', { rename, renameTask })
        return () => h('div')
      },
    }),
    { global: { plugins: [pinia, router] } }
  )
  return { routes, rename, renameTask, router, store: useWorkspaceStore() }
}

beforeEach(() => {
  api.createRoomTask.mockReset()
  api.createRoomTask.mockResolvedValue({ id: 'new-task' })
})

describe('侧栏频道菜单的四种入口和原有权限', () => {
  it.each([true, false])('已加入的活动频道只保留四项，can_manage=%s 也不出现静音或归档', (canManage) => {
    const { routes } = mount()
    const ids = routes.actionsFor(channel({ can_manage: canManage })).map((action) => action.id)
    expect(ids).toEqual(['topic.newTask', 'topic.allTasks', 'topic.rename', 'topic.leave'])
  })

  it('未加入的活动频道用加入替代退出，不额外禁止原本允许的改名', () => {
    const { routes } = mount()
    expect(routes.actionsFor(channel({ joined: false })).map((action) => action.id)).toEqual([
      'topic.newTask',
      'topic.allTasks',
      'topic.rename',
      'topic.join',
    ])
  })

  it.each([true, false])('综合不为凑够四项增加改名、退出或加入，joined=%s', (joined) => {
    const { routes } = mount()
    expect(routes.actionsFor(channel({ kind: 'root', joined, can_manage: true })).map((action) => action.id)).toEqual([
      'topic.newTask',
      'topic.allTasks',
    ])
  })

  it.each([true, false])('已归档频道不增加新建任务、改名或加入权限，can_manage=%s', (canManage) => {
    const { routes } = mount()
    expect(
      routes
        .actionsFor(channel({ status: 'archived', joined: false, can_manage: canManage }))
        .map((action) => action.id)
    ).toEqual(['topic.allTasks'])
    expect(
      routes.actionsFor(channel({ status: 'archived', joined: true, can_manage: canManage })).map((action) => action.id)
    ).toEqual(['topic.allTasks', 'topic.leave'])
  })

  it('改名回调收到被操作的频道，而不是当前页面的频道', () => {
    const { routes, rename } = mount()
    const target = channel()
    routes.actionsFor(target).find((action) => action.id === 'topic.rename')!.run!()
    expect(rename).toHaveBeenCalledExactlyOnceWith(target)
  })

  it.each([
    { joined: true, action: 'topic.leave', next: false },
    { joined: false, action: 'topic.join', next: true },
  ])('$action 回调只改变被操作频道的加入状态', ({ joined, action, next }) => {
    const { routes, store } = mount()
    const setJoined = vi.spyOn(store, 'setJoined').mockResolvedValue(true)
    routes.actionsFor(channel({ joined })).find((entry) => entry.id === action)!.run!()
    expect(setJoined).toHaveBeenCalledExactlyOnceWith('channel-a', next)
  })

  it('全部任务回调导航到被操作频道的任务筛选页', async () => {
    const { routes, router } = mount()
    routes.actionsFor(channel()).find((action) => action.id === 'topic.allTasks')!.run!()
    await waitFor(() => {
      expect(router.currentRoute.value.name).toBe('project-tasks')
      expect(router.currentRoute.value.params).toEqual({ projectId: 'project-a' })
      expect(router.currentRoute.value.query).toEqual({ channel: 'channel-a' })
    })
    expect(api.createRoomTask).not.toHaveBeenCalled()
  })

  it('新建任务回调在被操作频道创建任务并打开新任务', async () => {
    const { routes, router } = mount()
    routes.actionsFor(channel()).find((action) => action.id === 'topic.newTask')!.run!()
    await waitFor(() => {
      expect(api.createRoomTask).toHaveBeenCalledExactlyOnceWith('channel-a')
      expect(router.currentRoute.value.name).toBe('workspace-task')
      expect(router.currentRoute.value.params).toEqual({
        projectId: 'project-a',
        topicId: 'channel-a',
        taskId: 'new-task',
      })
    })
  })

  it('侧栏精简不删除共享菜单入口，不改变其他入口的管理权限', () => {
    const { router } = mount()
    const unmanaged = topicActions(channel(), router, { rename: vi.fn() }).map((action) => action.id)
    expect(unmanaged).toContain('topic.copyLink')
    expect(unmanaged).toContain('topic.mute')
    expect(unmanaged).toContain('topic.files')
    expect(unmanaged).not.toContain('topic.archive')
    const managed = topicActions(channel({ can_manage: true }), router, { rename: vi.fn() }).map((action) => action.id)
    expect(managed).toContain('topic.archive')
  })
})
