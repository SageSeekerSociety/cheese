// 底栏「空间」那一格底下的两页，各自落在哪儿。
//
// 小队以前落在「发现」上：那是有意图才去的一段（找队友），而从底栏点进来的人
// 是回自己队里。落地页选错的代价是每天都要多点一下，而且第一屏看到的是一片
// 和你无关的队伍。
import { createRouter, createWebHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AccountService from '@/services/account'

vi.mock('@/services/account', () => ({ default: { loggedIn: false } }))
vi.mock('@/layouts/home/Home.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/components/home/HomeSidebar.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/home/Landing.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/spaces/Index.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/teams/Index.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/teams/Mine.vue', () => ({ default: { template: '<div />' } }))

import home from './home'

const blank = { template: '<div />' }

function router() {
  return createRouter({
    history: createWebHistory(),
    routes: [home, { path: '/:pathMatch(.*)*', name: 'catch-all', component: blank }],
  })
}

describe('首页那一层', () => {
  beforeEach(() => {
    AccountService.loggedIn = false
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

  it('登录后根地址落在空间上', async () => {
    AccountService.loggedIn = true
    const r = router()
    await r.push('/')
    expect(r.currentRoute.value.name).toBe('HomeSpaces')
  })
})
