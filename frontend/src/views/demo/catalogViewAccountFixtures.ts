/**
 * 账号那一组整页条目（`catalogViewAccount.ts`）用的数据。
 *
 * 这些页面在应用里由各自的容器（`AccountSignIn.vue` 那一批）取数再摆进来，容器碰接口
 * 层，预览站碰不得。所以这里把容器算好的那几件按产品里的形状写死：一次第三方登录带回来
 * 的身份、当前生效的协议版本、进入方式的清单 —— 和 `catalogSharedFixtures.ts` 同一个
 * 理由：条目只吃 props，数据只有这一处。
 */
import type { AcceptedDocuments } from '@/network/api/legal/types'
import type { OAuthState } from '@/network/api/users/types'
import type { SignInWay } from '@/views/account/SignInView.vue'

/** 一次第三方登录带回来的身份：提供方、这个人、它建议填进表单的名字。 */
export const OAUTH_STATE: OAuthState = {
  providerId: 'school-sso',
  userInfo: {
    id: 'u-10231',
    email: 'wang@school.edu',
    name: '王小明',
    preferredUsername: 'wangxiaoming',
  },
  suggestedUsername: 'wangxiaoming',
  suggestedNickname: '王小明',
}

/** 后端当前的协议版本（`document key → 版本号`）：注册页要交给后端的那两份。 */
export const CONSENT_DOCUMENTS: AcceptedDocuments = {
  terms: '2026-01-01',
  privacy: '2026-01-01',
}

/** 登录页上「进入的方式」清单：容器按这台浏览器上次用了哪一样排过序。 */
export const SIGN_IN_WAYS: SignInWay[] = [
  { key: 'passkey', label: '用通行密钥登录', icon: 'mdi-fingerprint' },
  { key: 'email_code', label: '用邮箱验证码登录', icon: 'mdi-email-outline' },
  { key: 'oauth:school-sso', label: '用学校账号登录', icon: 'mdi-school-outline' },
]

/** 浏览器里登录走完之后，要把这份登录交给 app 的那个账号。 */
export const BACK_TO_APP_ACCOUNT = { name: '王小明', handle: 'wangxiaoming' }

/** 一封验证码发去了哪个地址：容器记下来，刷新不丢。 */
export const EMAIL_PENDING = { email: 'wang@school.edu' }
