// 登录完回到来的那一页。
//
// 一个带链接进来的人——项目页、设备审批页、预览页——被拦下要求登录，登录完
// 落在首页，那条链接就丢了，得自己再找一遍。SignIn 认 `?redirect=`，但跳去
// 登录页的地方有七八处（项目访问提示、侧栏那颗登录按钮、401 拦截器……），
// 各自记得带 redirect 是靠不住的：漏一处就是一条丢掉的链接。所以在路由层
// 统一补：去登录页而没带 redirect 的导航，把来路挂上。
import type { LocationQuery, NavigationGuardWithThis } from 'vue-router'

// 只认站内路径：`https://…` 和 `//evil.com` 都不要，否则登录页就是一个开放跳转。
export function postLoginTarget(query: LocationQuery): string {
  const r = query.redirect
  const path = Array.isArray(r) ? r[0] : r
  return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//') ? path : '/'
}

export const carryLoginRedirect: NavigationGuardWithThis<undefined> = (to, from) => {
  if (to.name !== 'SignIn' || to.query.redirect !== undefined) return true
  // 冷打开登录页时 from 是起点（path `/`）；从注册、找回密码这些页回到登录页，
  // 来路也不是一个值得回去的地方。
  if (from.matched.length === 0 || from.path === '/' || from.matched[0]?.name === 'Account') return true
  return { ...to, query: { ...to.query, redirect: from.fullPath } }
}

// OAuth 登录离开本站再回来，URL 上的 redirect 带不过去，先存起来。
const OAUTH_REDIRECT_KEY = 'oauth_redirect'

export function stashOAuthRedirect(path: string) {
  localStorage.setItem(OAUTH_REDIRECT_KEY, path)
}

export function takeOAuthRedirect(): string {
  const path = localStorage.getItem(OAUTH_REDIRECT_KEY)
  localStorage.removeItem(OAUTH_REDIRECT_KEY)
  return postLoginTarget({ redirect: path })
}
