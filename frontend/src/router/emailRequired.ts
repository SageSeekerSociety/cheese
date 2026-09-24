// 没有自己邮箱的账号，先补上邮箱才能继续——没有它，这个账号无法找回。
//
// 判据是服务端给的：自己的用户记录上 `emailMissing` 为真。登录有密码、通行密钥、
// 第三方、两步验证、令牌续签、冷打开恢复会话好几条路，它们都以「现在登着的是谁、
// 服务端怎么说」告终，所以这里只看登录身份，不挂在某个登录接口上。
//
// 两处拦：导航时（去哪儿都先去补邮箱，补完回到要去的地方），和标记在停留的页面上
// 才出现时（冷打开时 /users/me 晚于首屏回来、别的标签页登进来）。
import type { RouteLocationNormalized, Router } from 'vue-router'

import { watch } from 'vue'

export const ADD_EMAIL_ROUTE = 'AccountAddEmail'

// 登录的过程本身：会话还没落定（可能正从这一页的回跳里到来），走完再要求。
// 登录后提议添加通行密钥那一步也算在内：它手里的登录凭证只在登录后几分钟内有效。
// 协议页也放行：它们是这个要求之外的公开页面。
const PASSES = new Set([
  'SignIn',
  'SignInEmailCode',
  'SignInEmailCodeVerify',
  'PasskeyOffer',
  'SignUpStart',
  'SignUpVerifyEmail',
  'RecoverPasswordRequest',
  'RecoverPasswordVerify',
  'Verify2FA',
  'OAuthComplete',
  'OAuthVerify',
  'OAuthSuccess',
  'OAuthError',
  'LegalTerms',
  'LegalPrivacy',
])

export interface SignedInAccount {
  readonly loggedIn: boolean
  readonly user: { emailMissing?: boolean } | null
  readonly sessionRestored: Promise<void>
}

const needsEmail = (account: SignedInAccount) => account.loggedIn && account.user?.emailMissing === true

const passes = (route: RouteLocationNormalized) =>
  route.name === ADD_EMAIL_ROUTE || (typeof route.name === 'string' && PASSES.has(route.name))

const addEmail = (route: RouteLocationNormalized) => ({ name: ADD_EMAIL_ROUTE, query: { redirect: route.fullPath } })

/**
 * `account` 异步取：AccountService 的请求客户端反过来依赖路由，静态导入会成环
 * （router/home.ts 同理）。
 */
export function requireEmail(router: Router, account: () => Promise<SignedInAccount>) {
  router.beforeEach(async (to) => {
    const current = await account()
    if (to.name === ADD_EMAIL_ROUTE) {
      // 这一页只对登着的人有意义；冷打开时先等会话恢复完再判断。
      await current.sessionRestored
      return current.loggedIn ? true : { name: 'SignIn' }
    }
    return passes(to) || !needsEmail(current) ? true : addEmail(to)
  })

  void account().then((current) =>
    watch(
      () => needsEmail(current),
      async (need) => {
        if (!need) return
        await router.isReady()
        const here = router.currentRoute.value
        if (!passes(here)) await router.replace(addEmail(here))
      },
      { immediate: true }
    )
  )
}
