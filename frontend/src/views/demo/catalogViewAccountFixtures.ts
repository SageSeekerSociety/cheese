/**
 * 账号那一组整页条目（`catalogViewAccount.ts`）用的数据。
 *
 * 这些页面在应用里由各自的容器（`AccountSignIn.vue` 那一批）取数再摆进来，容器碰接口
 * 层，预览站碰不得。所以这里把容器算好的那几件按产品里的形状写死：一次第三方登录带回来
 * 的身份、当前生效的协议版本、进入方式的清单 —— 和 `catalogSharedFixtures.ts` 同一个
 * 理由：条目只吃 props，数据只有这一处。
 *
 * 这几件的类型从承载它们的页面组件 `$props` 里取，不从旧的网络层取：demo 源文件引用
 * `src/network` 会把 `legacy-network` 的账记上新的一笔。契约本就写在组件上，这里只是
 * 顺着它拿。
 */
import type OAuthCompleteView from '@/views/account/OAuthCompleteView.vue'
import type { SignInWay } from '@/views/account/SignInView.vue'

import { t } from '@/i18n'
import { oauthProviderIcon, oauthProviderName } from '@/views/account/oauthProvider'

type OAuthCompleteProps = InstanceType<typeof OAuthCompleteView>['$props']

/** 一次第三方登录带回来的身份。 */
type OAuthState = NonNullable<OAuthCompleteProps['oauthState']>
/** 后端当前的协议版本（`document key → 版本号`）。 */
type AcceptedDocuments = NonNullable<OAuthCompleteProps['consentDocuments']>

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

/** 这台浏览器上真会出现在登录页的那个第三方提供方。 */
const OAUTH_PROVIDER_ID = 'ruc'

/**
 * 登录页上「进入的方式」清单，按容器算出来的默认次序摆：容器先列第三方提供方，
 * 再是这台设备支不支持通行密钥，最后才是邮箱验证码 —— 和 `SignIn.vue` 里那个
 * `alternatives` 计算一模一样，文案和图标也复用产品自己的 `t`、`oauthProviderIcon`。
 */
export const SIGN_IN_WAYS: SignInWay[] = [
  {
    key: `oauth:${OAUTH_PROVIDER_ID}`,
    label: t('account.signIn.withProvider', { provider: oauthProviderName(OAUTH_PROVIDER_ID) }),
    icon: oauthProviderIcon(OAUTH_PROVIDER_ID),
  },
  { key: 'passkey', label: t('account.signIn.passkey'), icon: 'mdi-key-chain' },
  { key: 'email_code', label: t('account.signIn.emailCode'), icon: 'mdi-email-outline' },
]

/** 浏览器里登录走完之后，要把这份登录交给 app 的那个账号。 */
export const BACK_TO_APP_ACCOUNT = { name: '王小明', handle: 'wangxiaoming' }

/** 一封验证码发去了哪个地址：容器记下来，刷新不丢。 */
export const EMAIL_PENDING = { email: 'wang@school.edu' }
