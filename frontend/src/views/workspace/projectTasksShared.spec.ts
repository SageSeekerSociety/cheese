/** 看板和话题列表顶上那一行摘要读的是同一份活，同时开着就只读一份。
 *
 * 那一份是整个项目的活、每条带着简报和结论，一个大项目上有两兆。两边各拉各的，就是
 * 同一份数据隔几秒取两次；这里钉的是：一起打开只发一次，一起开着按快的那个（看板的
 * 15 秒）节奏读，摘要自己开着时照旧 30 秒读一次。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listProjectTasks: (...a: unknown[]) => listProjectTasks(...a),
    listProjectArtifacts: () => Promise.resolve({ data: [], total: 0 }),
    getProjectSite: () => Promise.resolve({ site: null, source_revision: null, candidates: [], can_publish: false }),
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ topics: [{ id: 'room-1', title: '运维', status: 'active' }], members: [] }),
}))

import BoardSummary from './BoardSummary.vue'
import RunningWorkView from './RunningWorkView.vue'

import { setLocale } from '@/i18n'

function task(over: Partial<RoomTask> = {}): RoomTask {
  return {
    id: 'task-1',
    project_id: 'p1',
    room_id: 'room-1',
    title: '查一下分页接口',
    status: 'open',
    owner_handle: 'ligan',
    created_at: '2026-08-23T01:00:00Z',
    updated_at: '2026-08-23T01:00:00Z',
    presentation: { column: 'needs_you', phrase: 'started' },
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({ data: [task()], total: 1 })
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

function mountBoard() {
  return render(RunningWorkView as unknown as Component, {
    props: { projectId: 'p1' },
    global: { plugins: [vuetify] },
  })
}

function mountSummary() {
  return render(BoardSummary as unknown as Component, {
    props: { projectId: 'p1' },
    global: { plugins: [vuetify] },
  })
}

describe('看板和摘要共用一次读', () => {
  it('一起打开只发一次请求，两边都画出这一份', async () => {
    const board = mountBoard()
    const summary = mountSummary()

    await board.findByText('查一下分页接口')
    await waitFor(() => expect(summary.container.querySelector('.board-summary')?.textContent).toMatch(/1/))
    expect(listProjectTasks).toHaveBeenCalledTimes(1)
  })

  it('一起开着时按看板的节奏读，摘要到点不另发', async () => {
    const board = mountBoard()
    mountSummary()
    await board.findByText('查一下分页接口')
    const opened = listProjectTasks.mock.calls.length

    await vi.advanceTimersByTimeAsync(60_000)
    // 60 秒里看板到点四次；摘要的两次（30 秒、60 秒）拿的都是看板刚读的那一份。
    expect(listProjectTasks.mock.calls.length - opened).toBe(4)
  })

  it('摘要自己开着时照旧 30 秒读一次', async () => {
    mountSummary()
    await waitFor(() => expect(listProjectTasks).toHaveBeenCalledTimes(1))

    await vi.advanceTimersByTimeAsync(60_000)
    expect(listProjectTasks).toHaveBeenCalledTimes(3)
  })
})
