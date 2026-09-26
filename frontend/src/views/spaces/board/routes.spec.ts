// 空间新界面那一棵路由。
//
// 两件事值得钉住，因为它们都不是「页面长什么样」而是**地址能不能被正确接住**：
//
// 1. 它是**并存**的一棵，挂在 `/spaces/:id/board` 下面 —— 老的空间页仍在
//    `/spaces/:id/tasks…` 上服务，所以这里的每条子路径都必须拼在 `board/` 后面，
//    不能长到 `/spaces/:id/tasks` 那一棵里去。
// 2. 管理员那三页的门槛是**在不在管理员名单里**，而角色是空间装完之后才算得出的。
//    守卫必须先等空间装好 —— 不等的话，管理员直接输地址会被当成普通成员弹回首页，
//    而且这个错只出现在「首次进入就是那一页」的情形里，点着走永远碰不到。
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// `vi.mock` 的工厂会被提升到文件最上面，所以里面不能碰下面才初始化的变量 ——
// 状态得先经过 `vi.hoisted` 立起来。
const hoisted = vi.hoisted(() => ({
  manager: false,
  // 装上空间之前谁都是普通成员 —— 这个替身把「角色是装完才算出来的」这件事
  // 真的演了一遍，而不是让 isManager 与 loadBoard 各说各话。
  // 签名先写清楚：`loadBoard` 收一个空间 id，后面 mockImplementation 才配得上。
  loadBoard: vi.fn<(id: number) => Promise<void>>(async () => {}),
}))

vi.mock('./store', () => ({
  // 真模块里 `isManager` 是个 computed ref，读的永远是 `.value` —— 这里给一个
  // 同形状的替身就够了，不必真的引 vue。
  isManager: {
    get value() {
      return hoisted.manager
    },
  },
  loadBoard: hoisted.loadBoard,
}))

const blank = { template: '<div />' }
vi.mock('./SpaceBoardShell.vue', () => ({ default: blank }))
vi.mock('./pages/BoardHome.vue', () => ({ default: blank }))
vi.mock('./pages/Mine.vue', () => ({ default: blank }))
vi.mock('./pages/Announcements.vue', () => ({ default: blank }))
vi.mock('./pages/Review.vue', () => ({ default: blank }))
vi.mock('./pages/Members.vue', () => ({ default: blank }))
vi.mock('./pages/TaskInsights.vue', () => ({ default: blank }))
// 第五批收进来的三处（底下都是老页面，包一层挂上来）。
vi.mock('./pages/TaskDetail.vue', () => ({ default: blank }))
vi.mock('./pages/TaskPublish.vue', () => ({ default: blank }))
vi.mock('./pages/Analytics.vue', () => ({ default: blank }))

// 那三层底下的真页面：这一份测的是**地址接没接住**，不是页面长什么样。不换掉的话，
// 每个用例都要现场编一遍 Vuetify 那一堆 SFC —— 六个地址跑下来直接超时，而我们想
// 知道的事（名字落在哪一格）跟编不编它们无关。
//
// 一条条写而不是循环：`vi.mock` 要能提上去，路径必须是字面量。
vi.mock('@/views/tasks/detail/Overview.vue', () => ({ default: blank }))
vi.mock('@/views/tasks/detail/Submissions.vue', () => ({ default: blank }))
vi.mock('@/views/tasks/detail/Participants.vue', () => ({ default: blank }))
vi.mock('@/views/tasks/detail/Submit.vue', () => ({ default: blank }))
vi.mock('@/views/tasks/detail/AIAdvice.vue', () => ({ default: blank }))

import { SpaceBoardRoutes } from './routes'

function router() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [SpaceBoardRoutes, { path: '/:pathMatch(.*)*', name: 'catch-all', component: blank }],
  })
}

async function open(path: string) {
  const r = router()
  await r.push(path)
  await r.isReady()
  return r.currentRoute.value
}

describe('空间新界面那一棵路由', () => {
  beforeEach(() => {
    hoisted.manager = false
    hoisted.loadBoard.mockReset()
    hoisted.loadBoard.mockImplementation(async () => {})
  })

  it('每条子页都拼在 /spaces/:id/board 后面', async () => {
    // 管理员那两页要先进得去，才谈得上「拼对没拼对」——它们各自的守卫另有用例。
    hoisted.manager = true
    expect((await open('/spaces/7/board')).fullPath).toBe('/spaces/7/board')
    expect((await open('/spaces/7/board/mine')).name).toBe('SpaceBoardMine')
    expect((await open('/spaces/7/board/announcements')).name).toBe('SpaceBoardAnnouncements')
    expect((await open('/spaces/7/board/insights/42')).name).toBe('SpaceBoardTaskInsights')
    expect((await open('/spaces/7/board/review')).name).toBe('SpaceBoardReview')
    expect((await open('/spaces/7/board/members')).name).toBe('SpaceBoardMembers')
  })

  it('详情那五格也在这棵树上，名字与外壳自己那一套对得上', async () => {
    hoisted.manager = true
    expect((await open('/spaces/7/board/tasks/42')).name).toBe('SpaceBoardTaskOverview')
    expect((await open('/spaces/7/board/tasks/42/submissions')).name).toBe('SpaceBoardTaskSubmissions')
    expect((await open('/spaces/7/board/tasks/42/participants')).name).toBe('SpaceBoardTaskParticipants')
    expect((await open('/spaces/7/board/tasks/42/submit')).name).toBe('SpaceBoardTaskSubmit')
    expect((await open('/spaces/7/board/tasks/42/ai-advice')).name).toBe('SpaceBoardTaskAIAdvice')
    expect((await open('/spaces/7/board/publish')).name).toBe('SpaceBoardTaskPublish')
  })

  it('看板是它自己那一页，不再是老树那九页的副本', async () => {
    hoisted.manager = true
    expect((await open('/spaces/7/board/analytics')).name).toBe('SpaceBoardAnalytics')
    // 那六条「把老分析页套进新外壳」的子路由没有了：留着也没人渲染。
    // 老树那九页仍在 `/spaces/:id/analytics/*` 上原样服务（那一棵不在这一份里）。
    expect((await open('/spaces/7/board/analytics/alerts')).name).toBe('catch-all')
  })

  it('看板对普通成员关上，直接输地址也进不去', async () => {
    expect((await open('/spaces/7/board/analytics')).name).toBe('SpaceBoardHome')
  })

  it('换一个空间，路径里的 id 跟着换（不是写死的）', async () => {
    expect((await open('/spaces/99/board/mine')).fullPath).toBe('/spaces/99/board/mine')
  })

  it('管理员那三页对普通成员关上，弹回首页', async () => {
    expect((await open('/spaces/7/board/review')).name).toBe('SpaceBoardHome')
    expect((await open('/spaces/7/board/members')).name).toBe('SpaceBoardHome')
  })

  it('管理员进得去，而且守卫先等空间装完', async () => {
    // 只有 7 号空间是「我的」：装完之后才成为管理员。守卫要是没 `await` 这一步，
    // 它看到的就是「还不是管理员」→ 弹回首页，这条用例会红。
    hoisted.loadBoard.mockImplementation(async (id: number) => {
      // 一次微任务 = 真接口那一跳。少了它，这个替身是同步变角色的，
      // 守卫等不等都看不出区别 —— 用例就变成了一条永远绿的摆设。
      await Promise.resolve()
      hoisted.manager = id === 7
    })
    expect((await open('/spaces/7/board/review')).name).toBe('SpaceBoardReview')
    expect(hoisted.loadBoard).toHaveBeenCalledWith(7)
  })
})
