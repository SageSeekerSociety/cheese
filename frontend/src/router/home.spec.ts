// 首页这一层的每一页各自落在哪儿。
//
// 小队以前落在「发现」上：那是有意图才去的一段（找队友），而从底栏点进来的人
// 是回自己队里。落地页选错的代价是每天都要多点一下，而且第一屏看到的是一片
// 和你无关的队伍。
//
// 根地址也一样：登录后回到上次待的那个项目——每天第一件事是接着干活。新账号一个
// 项目都没有，给它建一个，第一屏就是那个项目；建过一次的账号不再建，落在待办上。
import { createRouter, createWebHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'
import AccountService from '@/services/account'

const listProjects = vi.fn()
const createFirstProject = vi.fn()
vi.mock('@/api', () => ({
  listProjects: () => listProjects(),
  createFirstProject: (name: string) => createFirstProject(name),
}))

vi.mock('@/services/account', () => ({
  default: { loggedIn: false, sessionRestored: Promise.resolve(), user: { username: 'lin', nickname: '林' } },
}))
vi.mock('@/layouts/home/Home.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/home/HomeSidebar.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/home/HomeHub.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/InboxView.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/home/Landing.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/spaces/Index.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/teams/Index.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/teams/Mine.vue', () => ({ default: { template: '<div />' } }))

import home from './home'

const blank = { template: '<div />' }

function router() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      home,
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/projects/:projectId', name: 'workspace-project', component: blank },
      { path: '/projects/:projectId/channels/:topicId', name: 'workspace-topic', component: blank },
      { path: '/:pathMatch(.*)*', name: 'catch-all', component: blank },
    ],
  })
}

describe('首页那一层', () => {
  beforeEach(() => {
    localStorage.clear()
    listProjects.mockReset().mockResolvedValue({ data: [{ id: 'p1' }, { id: 'p2' }] })
    createFirstProject.mockReset().mockResolvedValue({ project_id: null })
    // 第一个项目的名字按这个人的语言写：这里读中文那一份。
    setLocale('zh-CN')
    AccountService.loggedIn = false
    AccountService.sessionRestored = Promise.resolve()
    delete (window as { __TAURI__?: unknown }).__TAURI__
  })
  it('小队落在「我的」上', async () => {
    const r = router()
    await r.push('/teams')
    expect(r.currentRoute.value.name).toBe('HomeTeamsMine')
  })

  it('未登录时根地址展示公开首页', async () => {
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('HomeDefault')
    expect(r.currentRoute.value.meta.publicLanding).toBe(true)
  })

  // 推广页是给还没进来的人看的。**只给未登录的人**：`titleKey` 和标题都是一句
  // 推广词，回访的人不该在根地址上看见它。
  it('登录后根地址回到上次待的那个项目', async () => {
    localStorage.setItem('cheesex.layout', JSON.stringify({ lastProjectId: 'p2' }))
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('workspace-project')
    expect(r.currentRoute.value.params.projectId).toBe('p2')
  })

  // 上次那个项目这个人已经不在里面了（或者存的是上一个登录者的）：不把他送去 403。
  it('上次那个项目不在自己的清单里时，落在自己的第一个项目上', async () => {
    localStorage.setItem('cheesex.layout', JSON.stringify({ lastProjectId: '别人的项目' }))
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.params.projectId).toBe('p1')
  })

  it('新账号登录后落在给它建好的第一个项目的「综合」上', async () => {
    listProjects
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValue({ data: [{ id: 'first', root_topic_id: 'general' }] })
    createFirstProject.mockResolvedValue({ project_id: 'first' })
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('workspace-topic')
    expect(r.currentRoute.value.params).toEqual({ projectId: 'first', topicId: 'general' })
    expect(createFirstProject).toHaveBeenCalledWith('林的项目')
  })

  // 第一个项目每个账号只给一次：退出或删光了项目的人不会再被塞一个。
  it('建过第一个项目、现在一个都没有的人落在待办上', async () => {
    listProjects.mockResolvedValue({ data: [] })
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('inbox')
  })

  it('有项目的人不会再得到一个', async () => {
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(createFirstProject).not.toHaveBeenCalled()
  })

  it('项目清单读不到时落在待办上，而不是停在推广页', async () => {
    listProjects.mockRejectedValue(new Error('offline'))
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('inbox')
  })

  it('空间列表这个地址还在', async () => {
    AccountService.loggedIn = true
    const r = router()
    await r.push('/spaces')
    expect(r.currentRoute.value.name).toBe('HomeSpaces')
  })

  // 我的工作那一页已经拆了：地址不再有，走到未知地址上。
  it('我的工作这一页不再存在', async () => {
    AccountService.loggedIn = true
    const r = router()
    await r.push('/work')
    expect(r.currentRoute.value.name).toBe('catch-all')
  })

  // 离开超过 15 分钟再回来：访问令牌过期，恢复会话要先换一个新的。换回来之前
  // loggedIn 是 false，根地址不能拿这个答案去决定——回访的人不该先看一眼推广页。
  it('会话还在恢复时根地址等它恢复完再落地', async () => {
    let restored!: () => void
    AccountService.sessionRestored = new Promise<void>((resolve) => (restored = resolve))
    const r = router()
    const navigation = r.push('/')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(r.currentRoute.value.name).toBeUndefined()
    AccountService.loggedIn = true
    restored()
    await navigation
    expect(r.currentRoute.value.name).toBe('workspace-project')
  })

  // 桌面 app 是已经装上的人在用：没登录就去登录，不看推广页。浏览器里照旧。
  it('桌面 app 里未登录时根地址去登录', async () => {
    ;(window as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } }
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('SignIn')
  })

  it('桌面 app 里登录后根地址同样回到项目', async () => {
    ;(window as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } }
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('workspace-project')
  })
})
