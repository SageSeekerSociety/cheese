// 审核页底部那张「最近处理过」的卡。它讲的是**过去发生的审核动作**，不是题目现在
// 的状态 —— 所以判据是接口那两列审核痕迹（`reviewedBy` / `reviewedAt`），而不是
// 题目的 `updatedAt`（改标题也刷它，拿它当审核时间会把「上个月改过简介」说成
// 「刚刚审过」）。
//
// 钉三件事：
//
// 1. **只列到 4 条，而且按审核时间倒序** —— 两块来源（通过的一批、驳回的一批）
//    合起来排，不是各排各的；
// 2. **一次审核痕迹都不在时给空态**，不是一张空白的卡；
// 3. **问接口要的是「按审核时间排」的那一列**，不是在客户端拿别的字段凑。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const listTasks = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listInviteCodes: vi.fn(async () => ({ data: { inviteCodes: [] } })),
  },
}))

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    list: (...a: unknown[]) => listTasks(...a),
    update: vi.fn(async () => ({ data: { task: {} } })),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { loadBoard } from '../store'

import Review from './Review.vue'

const SPACE_ID = 11
const REVIEWER = { id: 4, username: 'caisongyang', nickname: '蔡松洋' }

const SPACE = {
  id: SPACE_ID,
  name: '数据结构空间',
  intro: '',
  avatarId: null,
  admins: [{ user: REVIEWER, role: 'OWNER' }],
  announcements: '[]',
  taskTemplates: '[]',
  classificationTopics: [],
  visibleTaskLimit: null,
}

/** 审过的一道题：只有 `reviewedBy` / `reviewedAt` 那两格是这张卡的判据。 */
function reviewed(over: Record<string, unknown>) {
  return {
    id: 1,
    name: '一道题',
    intro: '简介',
    approved: 'APPROVED',
    participantLimit: 0,
    minTeamSize: 1,
    maxTeamSize: 1,
    deadline: null,
    createdAt: 1,
    participants: { total: 0, examples: [] },
    creator: { id: 9, username: 'author', nickname: '作者' },
    ...over,
  }
}

/** 时刻都取过去的固定值：`reviewedText` 按「是不是今天」换写法，而这些断言看的是
 *  顺序和 `<time datetime>`，不是那句中文。基准只取一次 —— 取两次的话下面那些
 *  「构造时期望的时刻」与「组件里那一格」会差几毫秒。 */
const BASE = Date.now()
const T = (minutesAgo: number) => BASE - minutesAgo * 60_000

/** 点名前先落一个登录态 —— 角色是拿它跟 `space.admins` 对出来的。 */
function signIn(handle: string) {
  localStorage.setItem('user', JSON.stringify({ id: 4, username: handle, nickname: '蔡松洋' }))
}

const Page = defineComponent({ render: () => h(RouterView) })

async function mount() {
  const stub = { render: () => h('div') }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board/review', name: 'SpaceBoardReview', component: Review as Component },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/board/review`)
  await router.isReady()

  const utils = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  // 审核页自己不装空间（那是外壳的活），这里补上 —— 审核人的名字就是从这份名册里
  // 对出来的。
  await loadBoard(SPACE_ID, true)
  return utils
}

/** 卡片上的四行（顺序即渲染顺序）。 */
function rows(): HTMLElement[] {
  return Array.from(document.querySelectorAll('.recent__row')) as HTMLElement[]
}

function emptyState(): Element | null {
  return document.querySelector('.recent__empty')
}

describe('审核页的「最近处理过」', () => {
  beforeEach(() => {
    localStorage.clear()
    spaceDetail.mockImplementation(async () => ({ data: { space: { ...SPACE } } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('两次查询（通过 / 驳回）合起来按审核时间倒序，只列最近 4 条', async () => {
    signIn('caisongyang')
    // 通过 3 条、驳回 3 条，时间交错：只按其中一次的返回顺序排，或者两边各取 4 条
    // 再拼起来，都会给出另一个顺序。
    const approved = [
      reviewed({ id: 101, name: '通过·最新', reviewedBy: 4, reviewedAt: T(1) }),
      reviewed({ id: 102, name: '通过·最早', reviewedBy: 4, reviewedAt: T(30) }),
      reviewed({ id: 103, name: '通过·中间', reviewedBy: 4, reviewedAt: T(10) }),
    ]
    const disapproved = [
      reviewed({ id: 201, name: '驳回·次新', approved: 'DISAPPROVED', reviewedBy: 4, reviewedAt: T(3) }),
      reviewed({ id: 202, name: '驳回·更早', approved: 'DISAPPROVED', reviewedBy: 4, reviewedAt: T(20) }),
      reviewed({ id: 203, name: '驳回·第二早', approved: 'DISAPPROVED', reviewedBy: 4, reviewedAt: T(15) }),
    ]
    listTasks.mockImplementation(async (params: { approved?: string }) => {
      if (params?.approved === 'APPROVED') return { data: { tasks: approved, page: {} } }
      if (params?.approved === 'DISAPPROVED') return { data: { tasks: disapproved, page: {} } }
      return { data: { tasks: [], page: {} } }
    })

    await mount()

    await waitFor(() => expect(rows()).toHaveLength(4))
    // 最旧的「驳回·更早」「通过·最早」被挤掉，剩下的是这四道，从新到旧。
    expect(rows().map((row) => row.querySelector('.recent__title')?.textContent)).toEqual([
      '通过·最新',
      '驳回·次新',
      '通过·中间',
      '驳回·第二早',
    ])
    // 每行都看得到结果与审核人，时间那一格带的是那一列的真值。
    const first = rows()[0]
    expect(first.textContent).toContain('通过')
    expect(first.textContent).toContain('蔡松洋')
    expect(first.querySelector('time')?.getAttribute('datetime')).toBe(new Date(T(1)).toISOString())
    // 一眼分得出驳回的那行与通过的那行不是同一件事。
    expect(rows()[1].textContent).toContain('驳回')
  })

  it('问接口要的是按审核时间排的那一列，而不是拿别的字段在客户端凑', async () => {
    signIn('caisongyang')
    listTasks.mockImplementation(async () => ({ data: { tasks: [], page: {} } }))

    await mount()

    for (const approved of ['APPROVED', 'DISAPPROVED']) {
      expect(listTasks).toHaveBeenCalledWith(
        expect.objectContaining({
          approved,
          sort_by: 'reviewedAt',
          sort_order: 'desc',
          pageSize: 4,
        })
      )
    }
  })

  it('一条审核痕迹都没有时给空态，而不是一行空白', async () => {
    signIn('caisongyang')
    // 老数据：审过但两列都是 null（这条迁移之前审的题），或者干脆没审过。
    const legacy = [reviewed({ id: 301, name: '很早以前审过的题', reviewedBy: null, reviewedAt: null })]
    listTasks.mockImplementation(async (params: { approved?: string }) => ({
      data: { tasks: params?.approved === 'APPROVED' ? legacy : [], page: {} },
    }))

    await mount()

    await waitFor(() => expect(emptyState()).not.toBeNull())
    expect(emptyState()?.textContent).toContain('暂无处理记录')
    expect(rows()).toHaveLength(0)
  })

  it('审核人被移出名册、名字对不出来时，这一行照旧在，只是说不知道是谁审的', async () => {
    signIn('caisongyang')
    listTasks.mockImplementation(async (params: { approved?: string }) => ({
      data: {
        tasks:
          params?.approved === 'APPROVED'
            ? [reviewed({ id: 401, name: '别人审的题', reviewedBy: 77, reviewedAt: T(2) })]
            : [],
        page: {},
      },
    }))

    await mount()

    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(rows()[0].textContent).toContain('别人审的题')
    expect(rows()[0].textContent).toContain('不知是谁审的')
  })
})
