// 单题看板的「小队构成」按**队名**分桶，名册里也写得出是哪支队伍 —— 出题人要的是
// 「哪几支队伍来了」，不是「有几支队伍」。
//
// 这张卡会悄悄错的只有两处，各钉一条：
//
// 1. **分桶的键是队名**。两支不同的队伍领了，就该有两行；把队名丢掉（或者还按名册
//    里那一列 `teamMembers` 判断团队）会让它们挤进同一行，这一条就红。
// 2. **拿不到队名时退回「小队」**。队已不在 / 老数据时后端只给 `isTeam`、不给
//    `team`，那一条仍然是一支队伍：落进「单人」就是把队伍说成了个人。
//
// 断言全落在屏幕上（那一格的两个桶名与人数、名册里的团队标），不看源码里怎么算。
// 接口在 `@/network/api/*` 那一层换掉 —— 真 axios 会被 `src/test/setup-network.ts`
// 逮住（未预期的 fetch 直接判失败）。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const taskDetail = vi.fn()
const getParticipants = vi.fn()
const getSubmissionQueue = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    detail: (...a: unknown[]) => taskDetail(...a),
    getParticipants: (...a: unknown[]) => getParticipants(...a),
  },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    getSubmissionQueue: (...a: unknown[]) => getSubmissionQueue(...a),
  },
}))

import TaskInsights from './Insights.vue'

const SPACE_ID = 7
const TASK_ID = 3

function task() {
  return {
    id: TASK_ID,
    name: '一道团队题',
    intro: '题',
    approved: 'APPROVED',
    participantLimit: 0,
    minTeamSize: 2,
    maxTeamSize: 3,
    deadline: null,
    createdAt: 1,
    creator: { id: 9, username: 'author', nickname: '出题人' },
    category: { id: 1, name: '默认分类' },
  }
}

/** 一条报名。`isTeam` / `team` 就是这条接口给的那两列。 */
function claim(over: Record<string, unknown>) {
  return {
    id: 1,
    member: { id: 1, name: '某人', intro: '', avatarId: null },
    createdAt: Date.now(),
    updatedAt: Date.now(),
    deadline: null,
    approved: 'APPROVED',
    isTeam: false,
    ...over,
  }
}

const stub = { render: () => null }

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId/insights', name: 'TasksInsights', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: stub },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/tasks/${TASK_ID}/insights`)
  await router.isReady()

  const utils = render(TaskInsights, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  // 题目是首屏那一块「接口回来之后」才有的东西 —— 等它出现，后面才有得量。
  await waitFor(() => expect(document.querySelector('.ins__kpis')).toBeTruthy())
  return utils
}

/** 一块面板：按标题找（这一页的面板不止一块）。 */
function panel(title: string): Element | undefined {
  return Array.from(document.querySelectorAll('.panel')).find(
    (p) => p.querySelector('h3')?.textContent?.trim() === title
  )
}

/** 「小队构成」那一格的桶：标签 → 「N 人」。排序不看，桶名与人数才是判据。 */
function buckets(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const row of Array.from(panel('小队构成')?.querySelectorAll('.bars__row') ?? [])) {
    const label = row.querySelector('.bars__label')?.textContent?.trim() ?? ''
    out[label] = row.querySelector('.bars__value')?.textContent?.trim() ?? ''
  }
  return out
}

/** 名册里每一行：谁、领的时候挂在哪支队伍上（单人没有这一格）。 */
function roster(): { name: string; team: string }[] {
  return Array.from(document.querySelectorAll('.roster li')).map((li) => ({
    name: li.querySelector('.roster__name')?.textContent?.trim() ?? '',
    team: li.querySelector('.roster__team')?.textContent?.trim() ?? '',
  }))
}

describe('单题看板的「小队构成」', () => {
  beforeEach(() => {
    taskDetail.mockImplementation(async () => ({ data: { task: task() } }))
    getSubmissionQueue.mockImplementation(async () => ({ data: { submissions: [] } }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('两支不同的队伍各占一行，名册里写的是队名', async () => {
    getParticipants.mockImplementation(async () => ({
      data: {
        participants: [
          claim({
            id: 1,
            isTeam: true,
            member: { id: 101, name: '玄武队', intro: '', avatarId: null },
            team: { id: 101, name: '玄武队' },
          }),
          claim({
            id: 2,
            isTeam: true,
            member: { id: 102, name: '朱雀队', intro: '', avatarId: null },
            team: { id: 102, name: '朱雀队' },
          }),
          claim({ id: 3, member: { id: 201, name: '林小满', intro: '', avatarId: null } }),
        ],
      },
    }))

    await mount()

    // 两队各一行 —— 按「是不是团队」分桶的话这里只有「小队 2 人」。
    expect(buckets()).toEqual({ 玄武队: '1 人', 朱雀队: '1 人', 单人: '1 人' })

    const rows = roster()
    expect(rows.find((r) => r.name === '玄武队')?.team).toBe('玄武队')
    expect(rows.find((r) => r.name === '朱雀队')?.team).toBe('朱雀队')
    // 单人那一行没有团队标。
    expect(rows.find((r) => r.name === '林小满')?.team).toBe('')
  })

  it('是团队但拿不到队名时退回「小队」，不掉进「单人」', async () => {
    getParticipants.mockImplementation(async () => ({
      data: {
        participants: [
          // 队已经不在（接口只给 isTeam，没有 team）＋ 一个按人领的。
          claim({ id: 1, isTeam: true, member: { id: 101, name: '', intro: '', avatarId: null } }),
          claim({ id: 2, member: { id: 201, name: '林小满', intro: '', avatarId: null } }),
        ],
      },
    }))

    await mount()

    expect(buckets()).toEqual({ 小队: '1 人', 单人: '1 人' })

    // 名册上那一行仍然挂着团队标（退回的是名字，不是「这是一支队伍」这件事）。
    const rows = roster()
    expect(rows).toHaveLength(2)
    expect(rows.filter((r) => r.team === '小队')).toHaveLength(1)
    expect(rows.find((r) => r.name === '林小满')?.team).toBe('')
  })
})
