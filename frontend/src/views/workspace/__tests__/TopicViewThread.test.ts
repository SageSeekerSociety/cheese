/** 点开一条支线，得真的打开它。
 *
 * 这一份补的是 `ChatPanelDispatched.test.ts` 停住的那一步。那一份断言到
 * 「点标题 → emit('open-topic', 'sub-1')」为止，**正好停在组件边界上**，而路由
 * 之后的那一跳才是坏的：TopicView 在 `store.topics` 里找这个 id，那张表来自
 * `GET /topics?project_id=`（只查 topics 表，只有房间），支线永远不在里面，于是
 * 一个完全好使的 id 渲染成「这个话题不存在」。整条路两个组件各自都是对的。
 *
 * 所以这里挂真实的 TopicView、真实的 store，只把网络那一层换掉——要钉的是
 * 「人点过去之后屏幕上是什么」。
 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getPlace = vi.fn()
const listTopics = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getPlace: (...a: unknown[]) => getPlace(...a),
    listTopics: (...a: unknown[]) => listTopics(...a),
    listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
    getTopicUnread: vi.fn().mockResolvedValue({}),
    getPrivateUnread: vi.fn().mockResolvedValue({}),
    markTopicRead: vi.fn().mockResolvedValue({}),
  }
})

import TopicView from '../TopicView.vue'

import { useWorkspaceStore } from '@/stores/workspace'

const View = TopicView as unknown as Component

const ROOM: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
}

const THREAD: RoomTask = {
  id: 'thread-1',
  project_id: 'p1',
  room_id: 'room-1',
  title: '查一下分页接口',
  status: 'open',
  owner_handle: 'cheese',
  created_at: '2026-08-23T01:00:00Z',
  updated_at: '2026-08-23T01:00:00Z',
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({}), text: async () => '' }))
  // 侧栏那份列表：只有房间，永远没有支线。这就是这个 bug 的土壤。
  listTopics.mockResolvedValue({ data: [ROOM] })
  const store = useWorkspaceStore()
  await store.openProject('p1')
})

function mount(topicId: string) {
  return render(View, {
    props: { projectId: 'p1', topicId },
    global: {
      plugins: [vuetify],
      // 这一份只关心「打开的是哪个地点」，下面那两栏各自会连 WS、建编辑器。
      stubs: { TopicChatColumn: true, WorkPanel: true },
      mocks: { $route: { query: {} } },
    },
  })
}

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

describe('打开一条支线', () => {
  it('侧栏列表里没有它，照样打得开', async () => {
    getPlace.mockResolvedValue(THREAD)
    const { queryByText, findByText } = mount('thread-1')

    expect(await findByText('查一下分页接口')).toBeTruthy()
    expect(queryByText('这个话题不存在')).toBeNull()
  })

  it('头部说得出它属于哪个房间', async () => {
    getPlace.mockResolvedValue(THREAD)
    const { findByTitle } = mount('thread-1')

    // 支线在侧栏里没有行，头部这条是唯一能说明「你在哪」也是唯一能走回去的路。
    expect(await findByTitle('回到 运维')).toBeTruthy()
  })

  it('房间还是照旧从列表里拿，不会为它多打一次请求', async () => {
    const { findByText } = mount('room-1')

    expect(await findByText('运维')).toBeTruthy()
    expect(getPlace).not.toHaveBeenCalled()
  })

  it('id 真的不存在时，才说不存在', async () => {
    getPlace.mockRejectedValue(new Error('404'))
    const { findByText } = mount('nope')

    expect(await findByText('这个话题不存在')).toBeTruthy()
  })

  it('取的过程中不先闪一下「不存在」', async () => {
    let release: (v: RoomTask) => void = () => {}
    getPlace.mockReturnValue(
      new Promise<RoomTask>((resolve) => {
        release = resolve
      })
    )
    const { queryByText, findByText } = mount('thread-1')

    // 还在路上：说「不存在」是在陈述一件还不知道的事。
    expect(queryByText('这个话题不存在')).toBeNull()
    release(THREAD)
    expect(await findByText('查一下分页接口')).toBeTruthy()
  })

  it('支线不记已读——支线的消息本来就不计进未读', async () => {
    getPlace.mockResolvedValue(THREAD)
    const api = await import('@/api')
    mount('thread-1')

    await waitFor(() => expect(getPlace).toHaveBeenCalled())
    expect(api.markTopicRead).not.toHaveBeenCalled()
  })
})
