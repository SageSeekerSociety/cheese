// 带链接进来、被拦下登录的人，登录完要回到那条链接上，而不是首页。
import { createRouter, createWebHistory } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import { carryLoginRedirect, postLoginTarget, stashOAuthRedirect, takeOAuthRedirect } from './loginRedirect'

const blank = { template: '<div />' }

function router() {
  const r = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'Home', component: blank },
      {
        path: '/account',
        name: 'Account',
        component: blank,
        children: [
          { path: 'signin', name: 'SignIn', component: blank },
          { path: 'signup', name: 'SignUpStart', component: blank },
        ],
      },
      { path: '/projects/:projectId/topics/:topicId', name: 'topic', component: blank },
    ],
  })
  r.beforeEach(carryLoginRedirect)
  return r
}

describe('去登录页的导航带上来路', () => {
  it('从一个项目页去登录，登录页记住那一页', async () => {
    const r = router()
    await r.push('/projects/7/topics/3?tab=files')
    await r.push('/account/signin')
    expect(r.currentRoute.value.name).toBe('SignIn')
    expect(r.currentRoute.value.query.redirect).toBe('/projects/7/topics/3?tab=files')
  })

  it('冷打开登录页没有来路', async () => {
    const r = router()
    await r.push('/account/signin')
    expect(r.currentRoute.value.query.redirect).toBeUndefined()
  })

  it('从首页去登录，登录完回首页就是默认，不用记', async () => {
    const r = router()
    await r.push('/')
    await r.push('/account/signin')
    expect(r.currentRoute.value.query.redirect).toBeUndefined()
  })

  it('从注册页回登录页，来路不算', async () => {
    const r = router()
    await r.push('/account/signup')
    await r.push('/account/signin')
    expect(r.currentRoute.value.query.redirect).toBeUndefined()
  })

  it('调用方自己带的 redirect 原样保留', async () => {
    const r = router()
    await r.push('/projects/7/topics/3')
    await r.push({ name: 'SignIn', query: { redirect: '/connect?code=abc' } })
    expect(r.currentRoute.value.query.redirect).toBe('/connect?code=abc')
  })
})

describe('登录完落在哪', () => {
  it('站内路径原样回去', () => {
    expect(postLoginTarget({ redirect: '/projects/7/topics/3?tab=files' })).toBe('/projects/7/topics/3?tab=files')
  })

  it('没带 redirect 落首页', () => {
    expect(postLoginTarget({})).toBe('/')
  })

  it('站外地址不认，登录页不能当开放跳转用', () => {
    expect(postLoginTarget({ redirect: 'https://evil.example/x' })).toBe('/')
    expect(postLoginTarget({ redirect: '//evil.example/x' })).toBe('/')
  })
})

describe('OAuth 出站再回来', () => {
  beforeEach(() => localStorage.clear())

  it('出站前存的地址回来时取走，只取一次', () => {
    stashOAuthRedirect('/projects/7')
    expect(takeOAuthRedirect()).toBe('/projects/7')
    expect(takeOAuthRedirect()).toBe('/')
  })
})
