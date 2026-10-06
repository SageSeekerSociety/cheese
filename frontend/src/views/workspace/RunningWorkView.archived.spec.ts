/** 已归档话题里的活不上三列。
 *
 * 活不归档，房间归档：房间收了尾，里面没走完的活（已退回、待回答）后端照样按自己的
 * 状态落在进行中 / 检查中 / 待处理里，一挂就是几周。三列答的是「接下来谁要动什么」，
 * 它们不该在上面，计数里也不该还算着。已完成照旧留着——交付过就是交付过。
 */
import type { Component } from 'vue'
import type { RoomTask } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  // 这些用例问的是板，所以清单是空的：那一块自己就不出现（它的行为在
  // `components/ArtifactManifest.spec.ts` 里）。不给这一条，组件会去真发一次请求。
  return {
    ...actual,
    getInbox: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listProjectTasks: (...a: unknown[]) => listProjectTasks(...a),
    listProjectArtifacts: () => Promise.resolve({ data: [], total: 0 }),
    getProjectSite: () => Promise.resolve({ site: null, source_revision: null, candidates: [], can_publish: false }),
  }
})

// 地址栏。测试改它，视图就该跟着变——这正是「开关住在地址里」的意思。
let query: Record<string, string> = {}
const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => ({
    get query() {
      return query
    },
  }),
}))

const handle = 'n1ctheboy'
vi.mock('@/me', () => ({ myHandle: () => handle }))

vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [
      { id: 'room-1', title: '运维', status: 'active' },
      { id: 'room-old', title: '收了尾的话题', status: 'archived' },
    ],
    members: [
      { user_handle: 'n1ctheboy', role: 'lead', name: '奶酪' },
      { user_handle: 'ligan', role: 'member', name: '李干' },
    ],
  }),
}))

import RunningWorkView from './RunningWorkView.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

const Board = RunningWorkView as unknown as Component

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
    presentation: { column: 'needs_you', phrase: 'awaiting_review' },
    ...over,
  }
}

const ROWS = [
  task({ id: 'a', title: '活着的待处理' }),
  task({
    id: 'b',
    title: '归档的待处理',
    room_id: 'room-old',
    presentation: { column: 'needs_you', phrase: 'bounced' },
  }),
  task({
    id: 'c',
    title: '归档的施工',
    room_id: 'room-old',
    presentation: { column: 'building', phrase: 'started' },
  }),
  task({
    id: 'd',
    title: '归档的交付',
    room_id: 'room-old',
    presentation: { column: 'delivering', phrase: 'awaiting_checks' },
  }),
  task({
    id: 'e',
    title: '归档的已完成',
    room_id: 'room-old',
    presentation: { column: 'done', phrase: 'accepted' },
  }),
]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  query = {}
  listProjectTasks.mockReset()
  listProjectTasks.mockResolvedValue({ data: ROWS, total: ROWS.length })
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function titlesInColumn(container: Element, column: string): string[] {
  return Array.from(container.querySelectorAll(`[data-column="${column}"] .board-card__title`)).map((n) =>
    (n.textContent ?? '').trim()
  )
}

function countOf(container: Element, column: string): string {
  return container.querySelector(`[data-column="${column}"] .board-col__count`)?.textContent?.trim() ?? ''
}

describe('已归档话题里的活', () => {
  it('不出现在进行中 / 检查中 / 待处理，列头计数也不算它们', async () => {
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'needs_you')).toEqual(['活着的待处理']))
    expect(titlesInColumn(container, 'building')).toEqual([])
    expect(titlesInColumn(container, 'delivering')).toEqual([])
    expect(countOf(container, 'needs_you')).toBe('1')
    expect(countOf(container, 'building')).toBe('0')
    expect(countOf(container, 'delivering')).toBe('0')
  })

  it('顶上那行统计也不算它们，但已完成照旧算', async () => {
    const { container } = mount()
    await waitFor(() => expect(titlesInColumn(container, 'needs_you')).toEqual(['活着的待处理']))
    expect(container.querySelector('.board__tally')?.textContent?.replace(/\s+/g, '')).toBe('待处理1·已完成1')
  })

  it('已完成里留着归档话题交付过的东西', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('.board__done-head')).not.toBeNull())
    await fireEvent.click(container.querySelector('.board__done-head') as HTMLElement)
    expect(container.querySelector('.board__done-list')?.textContent).toContain('归档的已完成')
  })

  it('「只看我的」的分母也不算它们', async () => {
    query = { mine: '1' }
    const { container } = mount()
    await waitFor(() => expect(countOf(container, 'needs_you')).toBe('0 / 1'))
  })
})
