// 任务的总览画的是它的实况文档 —— 这一格由房间页的 `#overview` 插槽填（`TopicView`），
// 文档那一半是接线外壳 `work/PanelDocHost`：面板自己只吃 props，取数在外壳里。
//
// 协同服务由 src/test/fakeDocCollab.ts 代替，和 `work/PanelDocHost.load.spec.ts` 同一套。
import type { Component } from 'vue'
import type { RoomTask, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetRooms, seedRoom } from '../../test/fakeDocCollab'

vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return { ...actual, getDocNodes: async () => ({ data: [], total: 0 }) }
})
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 这个任务的实况文档：测试里固定一篇，字在 fakeDocCollab 里按这个 id 预置。
  getRoomDocument: async () => ({ id: 'doc-1' }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))

import TaskOverview from './TaskOverview.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const Overview = TaskOverview as unknown as Component

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: `房间 ${id}`,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  } as Topic
}

function task(id: string, roomId: string): RoomTask {
  return {
    id,
    project_id: 'p1',
    room_id: roomId,
    title: '让文档先立起来',
    status: 'running',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  } as RoomTask
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  resetRooms()
})

afterEach(cleanup)

function open(task: RoomTask, extra: Record<string, unknown> = {}) {
  return render(Overview, {
    props: {
      room: room(task.room_id),
      task,
      memberNames: {},
      activityTick: 0,
      topicList: [],
      agentName: '芝士',
      agentHandle: 'cheese-t1',
      members: [],
      comparing: false,
      comparison: null,
      compareError: null,
      ...extra,
    },
    global: { plugins: [vuetify] },
  })
}

function prose(container: Element): string {
  return container.querySelector('.doc-prose')?.textContent ?? ''
}

describe('任务的总览', () => {
  it('画的是这个任务的实况文档', async () => {
    seedRoom('doc-1', '任务开工时写的第一段')
    const { container } = open(task('task-1', 't1'))

    await waitFor(() => expect(prose(container)).toContain('任务开工时写的第一段'))
  })

  // 「与开始时相比」是同一个位置的另一种画法：比对照在文档上面时，文档不该还在下面挂着。
  it('比对时文献比对顶掉文档', async () => {
    seedRoom('doc-1', '任务开工时写的第一段')
    const { container } = open(task('task-1', 't1'), {
      comparing: true,
      comparison: { before: '改之前', after: '改之后' },
    })

    await waitFor(() => expect(container.textContent).toContain('修改 1 段'))
    expect(container.querySelector('.task-overview__doc')).toBeNull()
  })
})
