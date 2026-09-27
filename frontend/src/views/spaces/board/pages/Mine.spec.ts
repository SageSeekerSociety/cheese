// 「我发布的」那一排 KPI 里的「平均完成率」。三件事各钉一条，都是这一格容易错的地方：
//
// 1. **分子分母是哪两个字段**。是 `successfulParticipantCount /
//    submittedParticipantCount`，两个数都来自这一页已经在读的那次 `me/publishing`
//    概览。不用 analytics 那条路上的成功数：它只认 `completion_status == SUCCESS`，
//    而这一列在新后端只有领取与逾期两处写入、恒不推进，算出来会是一张永远 0% 的卡。
//    所以第一条用例里「13 / 21」和题目自带的 `successRate`（0.5）刻意不同 —— 取错了
//    分子分母，断言就红。
// 2. **分母为 0 时是 0%**，不是 `NaN%` 也不是 `Infinity%`。一道题都没人交完全正常，
//    `0 / 0` 直接写进模板就是「NaN%」。
// 3. **这一排的顺序**：我出的题 / 累计被领取 / 等审核 / 平均完成率 / 等我判。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const myPublishingOverview = vi.fn()
const myPublishedTasks = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getMyPublishingOverview: (...a: unknown[]) => myPublishingOverview(...a),
    getMyPublishedTasks: (...a: unknown[]) => myPublishedTasks(...a),
    getMyParticipatingOverview: vi.fn(async () => ({ data: { spaceId: 11, participationCount: 0 } })),
    getMyParticipations: vi.fn(async () => ({ data: { participations: [] } })),
  },
}))

import Mine from './Mine.vue'

const SPACE_ID = 11

/** 「我发布的」那道题的**真形状**（页面只读其中几个字段，但形状要给全）。 */
function publishedTask(over: Record<string, unknown> = {}) {
  return {
    taskId: 1,
    taskName: '一道我出的题',
    category: { id: 1, name: '默认分类' },
    approved: 'APPROVED',
    visibilityStatus: 'PUBLISHED',
    isVisible: true,
    createdAt: Date.now(),
    publishedAt: Date.now(),
    endedAt: null,
    deadline: null,
    participantCount: 3,
    approvedParticipantCount: 3,
    pendingParticipantApprovalCount: 0,
    submittedParticipantCount: 2,
    pendingReviewCount: 0,
    successfulParticipantCount: 1,
    failedParticipantCount: 0,
    submissionConversionRate: 0.66,
    // 与「平均完成率」无关的一格（这是**单题**的成功率，0.5）。它在 13 / 21 下
    // 折成 50%，而卡面该显示 62% —— 两者不同，取错字段这条用例就会说话。
    successRate: 0.5,
    latestSubmissionAt: null,
    ...over,
  }
}

function overview(over: Record<string, unknown> = {}) {
  return {
    spaceId: SPACE_ID,
    taskCount: 4,
    approvedTaskCount: 3,
    pendingTaskApprovalCount: 1,
    disapprovedTaskCount: 0,
    participantCount: 9,
    approvedParticipantCount: 9,
    pendingParticipantApprovalCount: 0,
    submittedParticipantCount: 21,
    pendingReviewCount: 2,
    successfulParticipantCount: 13,
    ...over,
  }
}

const Page = defineComponent({ render: () => h(RouterView) })

async function mount() {
  const stub = { render: () => h('div') }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board/mine', name: 'SpaceBoardMine', component: Mine as Component },
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId/insights', name: 'SpaceBoardTaskInsights', component: stub },
      { path: '/spaces/:spaceId/board/publish', name: 'SpaceBoardTaskPublish', component: stub },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/board/mine`)
  await router.isReady()

  const utils = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  // KPI 那排要等 `refresh()` 那一轮请求回来才出现。
  await waitFor(() => expect(labels()).toContain('平均完成率'))
  return utils
}

/** 这一排所有 KPI 的 label，按屏幕上的先后。 */
function labels(): string[] {
  // `Array.from` 而不是展开：这份 tsconfig 的 lib 里没有 `DOM.Iterable`，展开一个
  // `NodeListOf` 会报 TS2488。
  return Array.from(document.querySelectorAll('.metric__label')).map((el) => (el.textContent || '').trim())
}

/** 按 label 取一张卡的卡面。 */
function card(label: string) {
  const hit = Array.from(document.querySelectorAll('.metric')).find(
    (el) => (el.querySelector('.metric__label')?.textContent || '').trim() === label
  )
  if (!hit) return null
  return {
    value: (hit.querySelector('.metric__value')?.textContent || '').trim(),
    hint: (hit.querySelector('.metric__hint')?.textContent || '').trim(),
    icon: hit.querySelector('.metric__icon')?.className || '',
  }
}

describe('「我的」那一排 KPI', () => {
  beforeEach(() => {
    localStorage.clear()
    myPublishingOverview.mockImplementation(async () => ({ data: overview() }))
    myPublishedTasks.mockImplementation(async () => ({ data: { tasks: [publishedTask()] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('平均完成率是「已通过 / 已提交」，措辞与图标与原型一致', async () => {
    await mount()

    const rate = card('平均完成率')
    expect(rate).not.toBeNull()
    // 13 / 21 = 0.619… → 62%（取整）。不是那道题的 successRate 折出来的 50%。
    expect(rate?.value).toBe('62%')
    expect(rate?.hint).toBe('已通过 / 已提交')
    expect(rate?.icon).toContain('mdi-progress-check')
    // 分子分母真的取自那两个字段：换掉它们，卡面就跟着换。
    expect(myPublishingOverview).toHaveBeenCalledWith(SPACE_ID)

    myPublishingOverview.mockImplementation(async () => ({
      data: overview({ successfulParticipantCount: 3, submittedParticipantCount: 4 }),
    }))
    cleanup()
    await mount()
    expect(card('平均完成率')?.value).toBe('75%')
  })

  it('一个都没交过时是 0%，不是 NaN%', async () => {
    myPublishingOverview.mockImplementation(async () => ({
      data: overview({ successfulParticipantCount: 0, submittedParticipantCount: 0 }),
    }))
    await mount()

    expect(card('平均完成率')?.value).toBe('0%')
    expect(document.body.textContent).not.toContain('NaN')
    expect(document.body.textContent).not.toContain('Infinity')
  })

  it('这一排是 5 张卡，平均完成率夹在「等审核」与「等我判」之间', async () => {
    await mount()

    expect(labels()).toEqual(['我出的题', '累计被领取', '等审核', '平均完成率', '等我判'])
  })
})
