// 登录完回到来的那一页。
//
// 一个带链接进来的人——项目页、设备审批页、预览页——被拦下要求登录，登录完
// 落在首页，那条链接就丢了，得自己再找一遍。SignIn 认 `?redirect=`，但跳去
// 登录页的地方有七八处（项目访问提示、侧栏那颗登录按钮、401 拦截器……），
// 各自记得带 redirect 是靠不住的：漏一处就是一条丢掉的链接。所以在路由层
// 统一补：去登录页而没带 redirect 的导航，把来路挂上。
import type { LocationQuery, NavigationGuardWithThis } from 'vue-router'

import { storageGet, storageRemove, storageSet } from '@/lib/browserStorage'

// 只认站内路径：`https://…`、`//evil.com` 都不要，否则登录页就是一个开放跳转。
// 按浏览器的解析规则判，不按字面：浏览器把 `\` 当 `/`、丢掉制表符和换行，
// `/\evil.com` 字面上是站内路径，交给 `location.replace` 却会出站。
// 拿一个占位的源来解析，解析完源没变才算站内。放行的是原串，不是解析后的
// pathname：解析会折叠 `.` 段，`/.//evil.com` 折叠完是 `//evil.com`，判过的和交出去
// 的必须是同一个值。
const SAME_ORIGIN = 'https://same-origin.invalid'

function staysOnSite(path: string): boolean {
  try {
    return new URL(path, SAME_ORIGIN).origin === SAME_ORIGIN
  } catch {
    return false // 浏览器也解析不了的地址（主机名不合法），不是一个能回去的页
  }
}

export function postLoginTarget(query: LocationQuery): string {
  const r = query.redirect
  const path = Array.isArray(r) ? r[0] : r
  return typeof path === 'string' && path.startsWith('/') && staysOnSite(path) ? path : '/'
}

export const carryLoginRedirect: NavigationGuardWithThis<undefined> = (to, from) => {
  if (to.name !== 'SignIn' || to.query.redirect !== undefined) return true
  // 冷打开登录页时 from 是起点（path `/`）；从注册、找回密码这些页回到登录页，
  // 来路也不是一个值得回去的地方。
  if (from.matched.length === 0 || from.path === '/' || from.matched[0]?.name === 'Account') return true
  return { ...to, query: { ...to.query, redirect: from.fullPath } }
}

// The gateway can serve other pages on this origin beside the app (a static
// portal, say). A sign-in sent back to one of them would land on the app's 404,
// because the router owns every path it is asked for. So a navigation out of the
// account pages to a path no route of the app claims is handed to the browser.
// The cold open of such a path is left alone: the server already answered it
// with the app, and loading it again would only repeat that.
// `location.replace`, not `assign`: every sign-in completion replaces the sign-in
// page in history, and Back must not return to a page that only sends you on.
export const leaveAppForPagesItHasNot: NavigationGuardWithThis<undefined> = (to, from) => {
  const fromAccount = [from, to.redirectedFrom].some((r) => r?.matched[0]?.name === 'Account')
  if (!fromAccount || !to.matched.every((r) => r.name === 'NotFound')) return true
  window.location.replace(to.fullPath)
  return false
}

// 已经登录的人打开登录页（收藏的地址、浏览器后退、另一个标签页里登录过）：没有什么
// 要他做的，直接送他去登录完本来要去的地方，`?redirect=` 或首页。`signedIn` 要等冷
// 打开时的会话恢复完再答，否则一个令牌刚过期、正在换新的人会被当成没登录。
export function leaveSignInWhenSignedIn(signedIn: () => Promise<boolean>): NavigationGuardWithThis<undefined> {
  return async (to) => ((await signedIn()) ? postLoginTarget(to.query) : true)
}

// OAuth 登录离开本站再回来，URL 上的 redirect 带不过去，先存起来。
const OAUTH_REDIRECT_KEY = 'oauth_redirect'

export function stashOAuthRedirect(path: string) {
  storageSet(OAUTH_REDIRECT_KEY, path)
}

export function takeOAuthRedirect(): string {
  const path = storageGet(OAUTH_REDIRECT_KEY)
  storageRemove(OAUTH_REDIRECT_KEY)
  return postLoginTarget({ redirect: path })
}

// 登录页每次打开都是一次新的登录：上一次 OAuth 出站没走完留下的地址不能带进来。
export function forgetOAuthRedirect() {
  storageRemove(OAUTH_REDIRECT_KEY)
}
