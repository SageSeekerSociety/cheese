// 刚建出来的项目落在看板上时，看到的是什么。
//
// 「四列都空」和「这个项目还没开始」在屏幕上长得一样，但它们是两回事：前者是常态
// ——活干完了归进已完成，板照常回答「什么在跑」；后者连这个问题都还不成立。一个
// 第一次用这个产品的人如果第一屏是四列「暂无」，他没有任何线索该往哪儿走。
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()
const push = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjectTasks: (...a: unknown[]) => listProjectTasks(...a) }
})
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

// vi.mock 会被提升到文件顶部，所以这个共享对象必须用 vi.hoisted 一起提上去，
// 否则工厂运行时它还没初始化。
const store = vi.hoisted(() => ({
  topics: [] as unknown[],
  members: [] as unknown[],
  loadingTopics: false,
  rootTopic: null as unknown,
}))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import RunningWorkView from './RunningWorkView.vue'

const Board = RunningWorkView as unknown as Component
const ROOT = { id: 'root-1', title: '项目总览', kind: 'root' }
const ROOM = { id: 'room-1', title: '第一周的调研', kind: 'topic' }

function task(): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    owner_handle: 'ligan',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'building', display_status: '运行中' },
  } as RoomTask
}

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }),
  })
})
beforeEach(() => {
  vi.clearAllMocks()
  store.topics = [ROOT]
  store.loadingTopics = false
  store.rootTopic = ROOT
  listProjectTasks.mockResolvedValue({ data: [] })
})
afterEach(cleanup)

function mount() {
  return render(Board, {
    props: { projectId: 'p1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('a project with nothing in it is told where to start', async () => {
  mount()

  expect(await screen.findByText('这个项目还没有开始的工作')).toBeTruthy()
  expect(await screen.findByText('进入对话')).toBeTruthy()
})

it('the way in actually goes to the home room', async () => {
  mount()

  await fireEvent.click(await screen.findByText('进入对话'))

  expect(push).toHaveBeenCalledWith({
    name: 'workspace-topic',
    params: { projectId: 'p1', topicId: 'root-1' },
  })
})

it('a project that has rooms keeps the board, even with every column empty', async () => {
  // 三列全空是常态（活都归进已完成了），板照常回答「什么在跑」。
  store.topics = [ROOT, ROOM]
  mount()

  await waitFor(() => expect(screen.getByText('施工中')).toBeTruthy())
  expect(screen.queryByText('这个项目还没有开始的工作')).toBeNull()
})

it('a project with work keeps the board', async () => {
  listProjectTasks.mockResolvedValue({ data: [task()] })
  store.topics = [ROOT, ROOM]
  mount()

  await waitFor(() => expect(screen.getByText('查一下分页接口')).toBeTruthy())
  expect(screen.queryByText('这个项目还没有开始的工作')).toBeNull()
})

it('nothing is claimed while the topics are still loading', async () => {
  // 话题还在路上时就说「还没有开始」，会在加载完成的瞬间闪掉——比晚一点更糟。
  store.loadingTopics = true
  mount()

  await waitFor(() => expect(listProjectTasks).toHaveBeenCalled())
  expect(screen.queryByText('这个项目还没有开始的工作')).toBeNull()
})
