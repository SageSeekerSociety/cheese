// 带链接进来、被拦下登录的人，登录完要回到那条链接上，而不是首页。
import { createRouter, createWebHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  carryLoginRedirect,
  leaveAppForPagesItHasNot,
  leaveSignInWhenSignedIn,
  postLoginTarget,
  stashOAuthRedirect,
  takeOAuthRedirect,
} from './loginRedirect'

const blank = { template: '<div />' }

function router(signedIn = false) {
  const r = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'Home', component: blank },
      {
        path: '/account',
        name: 'Account',
        component: blank,
        children: [
          {
            path: 'signin',
            name: 'SignIn',
            component: blank,
            beforeEnter: leaveSignInWhenSignedIn(async () => signedIn),
          },
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
    expect(postLoginTarget({ redirect: '/\\evil.example/x' })).toBe('/')
    expect(postLoginTarget({ redirect: '/\t/evil.example/x' })).toBe('/')
    expect(postLoginTarget({ redirect: '/\\evil.example%40okcheese.com' })).toBe('/')
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

describe('已经登录的人打开登录页', () => {
  it('回到他本来要去的那一页', async () => {
    const r = router(true)
    await r.push('/account/signin?redirect=/projects/7/topics/3')
    expect(r.currentRoute.value.fullPath).toBe('/projects/7/topics/3')
  })

  it('没有要去的地方就回首页', async () => {
    const r = router(true)
    await r.push('/account/signin')
    expect(r.currentRoute.value.name).toBe('Home')
  })

  it('站外的 redirect 不认，也回首页', async () => {
    const r = router(true)
    await r.push('/account/signin?redirect=//evil.example/x')
    expect(r.currentRoute.value.name).toBe('Home')
  })

  it('没登录的人照常看到登录页', async () => {
    const r = router(false)
    await r.push('/account/signin')
    expect(r.currentRoute.value.name).toBe('SignIn')
  })
})

// 同一个域名下网关还挂着别的页（静态门户之类）：登录完要回那一页，得让浏览器去取，
// 不能在应用里落到 404。
describe('登录完要去的页不是应用里的页', () => {
  const pageLoad = vi.fn()

  beforeEach(() => {
    pageLoad.mockClear()
    vi.stubGlobal('location', { ...window.location, replace: pageLoad })
  })
  afterEach(() => vi.unstubAllGlobals())

  function site(signedIn = false) {
    const r = createRouter({
      history: createWebHistory(),
      routes: [
        { path: '/', name: 'Home', component: blank },
        {
          path: '/account',
          name: 'Account',
          component: blank,
          children: [
            {
              path: 'signin',
              name: 'SignIn',
              component: blank,
              beforeEnter: leaveSignInWhenSignedIn(async () => signedIn),
            },
            { path: 'oauth/success', name: 'OAuthSuccess', component: blank },
          ],
        },
        { path: '/projects/:projectId', name: 'project', component: blank },
        { path: '/:pathMatch(.*)*', name: 'NotFound', component: blank },
      ],
    })
    r.beforeEach(carryLoginRedirect)
    r.beforeEach(leaveAppForPagesItHasNot)
    return r
  }

  // 登录页登录成功后做的就是这一步：`router.replace(postLoginTarget(route.query))`。
  async function signInFrom(query: string) {
    const r = site()
    await r.push(`/account/signin${query}`)
    await r.replace(postLoginTarget(r.currentRoute.value.query))
    return r
  }

  it('应用没有的同源页，整页打开它，查询参数原样带上', async () => {
    await signInFrom('?redirect=/portal.html%3Fgrade%3D2025')
    expect(pageLoad).toHaveBeenCalledWith('/portal.html?grade=2025')
  })

  it('应用里的页照常在应用里打开', async () => {
    const r = await signInFrom('?redirect=/projects/7')
    expect(r.currentRoute.value.fullPath).toBe('/projects/7')
    expect(pageLoad).not.toHaveBeenCalled()
  })

  it('站外地址仍然只回首页', async () => {
    for (const evil of ['//evil.example/x', 'https://evil.example/x', '/\\evil.example/x', '/\t/evil.example/x']) {
      const r = await signInFrom(`?redirect=${encodeURIComponent(evil)}`)
      expect(r.currentRoute.value.name).toBe('Home')
    }
    expect(pageLoad).not.toHaveBeenCalled()
  })

  it('带点段的地址整页打开时也落在本站', async () => {
    for (const odd of ['/.//evil.example', '/..//evil.example', '/x/..//evil.example']) {
      pageLoad.mockClear()
      await signInFrom(`?redirect=${encodeURIComponent(odd)}`)
      expect(pageLoad).toHaveBeenCalledOnce()
      const [sent] = pageLoad.mock.calls[0]
      expect(new URL(sent, 'https://okcheese.com/account/signin').origin).toBe('https://okcheese.com')
    }
  })

  it('OAuth 回来取出存下的那一页，也整页打开', async () => {
    localStorage.clear()
    stashOAuthRedirect('/catalog.html')
    const r = site()
    await r.push('/account/oauth/success')
    await r.replace(takeOAuthRedirect())
    expect(pageLoad).toHaveBeenCalledWith('/catalog.html')
  })

  it('已经登录的人打开登录页，直接整页去那一页', async () => {
    await site(true).push('/account/signin?redirect=/portal.html')
    expect(pageLoad).toHaveBeenCalledWith('/portal.html')
  })

  it('直接打开一个应用没有的地址，服务器已经给了应用，就显示 404，不再重载', async () => {
    const r = site()
    await r.push('/portal.html')
    expect(r.currentRoute.value.name).toBe('NotFound')
    expect(pageLoad).not.toHaveBeenCalled()
  })
})
