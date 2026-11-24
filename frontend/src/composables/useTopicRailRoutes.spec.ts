// 侧栏右键列的是频道那一整份（和命令面板、手机顶栏的 ⋯ 同一份），权限照旧；回调对准被操作的频道。
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

import { channelDetailsRequest } from '@/commands/channelDetails'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
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
  const closeTask = vi.fn()
  let routes!: ReturnType<typeof useTopicRailRoutes>
  render(
    defineComponent({
      setup() {
        routes = useTopicRailRoutes(() => 'project-a', { rename, renameTask, closeTask })
        return () => h('div')
      },
    }),
    { global: { plugins: [pinia, router] } }
  )
  return { routes, rename, renameTask, closeTask, router, store: useWorkspaceStore() }
}

beforeEach(() => {
  api.createRoomTask.mockReset()
  api.createRoomTask.mockResolvedValue({ id: 'new-task' })
})

const ids = (actions: { id: string }[]) => actions.map((action) => action.id)

describe('侧栏右键：频道那一整份', () => {
  it.each([true, false])('已加入的活动频道：已读通知链接、任务、频道本身、退出，can_manage=%s 一样', (canManage) => {
    const { routes } = mount()
    expect(ids(routes.actionsFor(channel({ can_manage: canManage })))).toEqual([
      'topic.notify',
      'topic.copyLink',
      'topic.newTask',
      'topic.allTasks',
      'topic.details',
      'topic.rename',
      'topic.leave',
    ])
  })

  it('每组之间一条分隔线', () => {
    const { routes } = mount()
    const apart = routes
      .actionsFor(channel())
      .filter((action) => action.divider)
      .map((action) => action.id)
    expect(apart).toEqual(['topic.newTask', 'topic.details', 'topic.leave'])
  })

  it('未加入的频道没有通知，用加入替代退出', () => {
    const { routes } = mount()
    expect(ids(routes.actionsFor(channel({ joined: false })))).toEqual([
      'topic.copyLink',
      'topic.newTask',
      'topic.allTasks',
      'topic.details',
      'topic.rename',
      'topic.join',
    ])
  })

  it.each([true, false])('综合只有前两组和频道详情，joined=%s', (joined) => {
    const { routes } = mount()
    const expected = [
      ...(joined ? ['topic.notify'] : []),
      'topic.copyLink',
      'topic.newTask',
      'topic.allTasks',
      'topic.details',
    ]
    expect(ids(routes.actionsFor(channel({ kind: 'root', joined, can_manage: true })))).toEqual(expected)
  })

  it.each([true, false])('已归档频道：复制链接、全部任务、详情，还在里面的能退出，can_manage=%s', (canManage) => {
    const { routes } = mount()
    expect(ids(routes.actionsFor(channel({ status: 'archived', joined: false, can_manage: canManage })))).toEqual([
      'topic.copyLink',
      'topic.allTasks',
      'topic.details',
    ])
    expect(ids(routes.actionsFor(channel({ status: 'archived', joined: true, can_manage: canManage })))).toEqual([
      'topic.copyLink',
      'topic.allTasks',
      'topic.details',
      'topic.leave',
    ])
  })

  it('归档、取消归档、删除不在这一份里，在频道详情的「设置」里', () => {
    const { routes } = mount()
    for (const over of [{ can_manage: true }, { can_manage: true, status: 'archived' as const }]) {
      const listed = ids(routes.actionsFor(channel(over)))
      expect(listed).not.toContain('topic.archive')
      expect(listed).not.toContain('topic.unarchive')
      expect(listed).not.toContain('topic.delete')
    }
  })

  it('「通知」点开是三档和静音多久，此刻那一档打勾，选了就改这个频道', () => {
    const { routes, store } = mount()
    const setNotifyLevel = vi.spyOn(store, 'setNotifyLevel').mockResolvedValue()
    const notify = routes.actionsFor(channel()).find((action) => action.id === 'topic.notify')!
    const levels = notify.children!.slice(0, 3)
    expect(levels.map((level) => level.checked)).toEqual([false, true, false])
    levels[0].run!()
    expect(setNotifyLevel).toHaveBeenCalledExactlyOnceWith('channel-a', 'all', null)
  })

  it('「频道详情」先去那个频道，由它的页头打开', async () => {
    const { routes, router } = mount()
    await router.addRoute({
      path: '/projects/:projectId/topics/:topicId',
      name: 'workspace-topic',
      component: { render: () => null },
    })
    routes.actionsFor(channel()).find((action) => action.id === 'topic.details')!.run!()
    await waitFor(() =>
      expect(router.currentRoute.value.params).toEqual({ projectId: 'project-a', topicId: 'channel-a' })
    )
    expect(channelDetailsRequest.value).toEqual({ topicId: 'channel-a', tab: 'about' })
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
})

describe('侧栏右键：任务那一份', () => {
  const task = (over: Record<string, unknown> = {}) => ({
    id: 'task-1',
    project_id: 'project-a',
    room_id: 'channel-a',
    status: 'open',
    owner_handle: 'someone-else',
    ...over,
  })

  it('不参加这件任务的人只有复制链接', () => {
    const { routes } = mount()
    expect(ids(routes.taskActionsFor(task()))).toEqual(['task.copyLink'])
  })

  it('关了的任务，项目里角色能新建任务和审阅的人能重新打开', () => {
    const { routes, store } = mount()
    queryClient.setQueryData(keys.projects(), [{ id: 'project-a', name: 'A', my_permissions: ['tasks'] }])
    const reopen = vi.spyOn(store, 'reopenTask').mockResolvedValue(true)
    const actions = routes.taskActionsFor(task({ status: 'closed' }))
    expect(ids(actions)).toEqual(['task.copyLink', 'task.reopen'])
    actions[1].run!()
    expect(reopen).toHaveBeenCalledExactlyOnceWith('task-1')
  })
})
