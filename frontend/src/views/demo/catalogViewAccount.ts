/**
 * 预览站里「账号那一组整页」：从登录进门到设置密码、补邮箱、改密码的每一屏，加上
 * 404 兜底页和文档站的登录页。
 *
 * 这些页面在应用里都有各自的容器取数（`AccountSignIn.vue`、`AccountSignUp.vue` 那一批），
 * 容器碰接口层，预览站碰不得。好在它们都已经被拆成「容器 → 只吃 props 的页面」：这里展示的
 * 正是产品里真会走到的那几格 —— 还没打开浏览器 / 打开过一次、还在跑 / 出错、换备用码 /
 * 用了备用码之后、填地址 / 验证码发出去之后。各屏公用的一份数据见
 * `catalogViewAccountFixtures.ts`。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限 —— 和
 * `catalogAdmin.ts`、`catalogViews.ts` 同一个理由。这里的 `CatalogEntry`、`CatalogNeed`
 * 是 type-only 引用：`catalog.ts` 反过来要分册的值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  BACK_TO_APP_ACCOUNT,
  CONSENT_DOCUMENTS,
  EMAIL_PENDING,
  OAUTH_STATE,
  SIGN_IN_WAYS,
} from './catalogViewAccountFixtures'

import { t } from '@/i18n'
import Views404 from '@/views/404.vue'
import AddEmailView from '@/views/account/AddEmailView.vue'
import AppSignInFinishView from '@/views/account/AppSignInFinishView.vue'
import AppSignInStartView from '@/views/account/AppSignInStartView.vue'
import BackToAppView from '@/views/account/BackToAppView.vue'
import DesktopSignInView from '@/views/account/DesktopSignInView.vue'
import RequestView from '@/views/account/emailCode/RequestView.vue'
import EmailCodeVerifyView from '@/views/account/emailCode/VerifyView.vue'
import OAuthCompleteView from '@/views/account/OAuthCompleteView.vue'
import OAuthErrorView from '@/views/account/OAuthErrorView.vue'
import OAuthSuccessView from '@/views/account/OAuthSuccessView.vue'
import OAuthVerifyView from '@/views/account/OAuthVerifyView.vue'
import PasskeyOfferView from '@/views/account/PasskeyOfferView.vue'
import PasswordStartView from '@/views/account/recover/password/StartView.vue'
import PasswordVerifyView from '@/views/account/recover/password/VerifyView.vue'
import SignInView from '@/views/account/SignInView.vue'
import SignupStartView from '@/views/account/signup/StartView.vue'
import VerifyEmailView from '@/views/account/signup/VerifyEmailView.vue'
import Verify2FAView from '@/views/account/Verify2FAView.vue'
import DocsSignInView from '@/views/DocsSignInView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面两样，再加一条 name 指得上的路由（按钮的 `to`、链接的去处按名字解析）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 页面里「验证码对不对」「再发一封」两趟接口由容器给，预览站上走一遍就完。 */
const ok = async () => {}
const done = async () => undefined

export const VIEW_ACCOUNT_ENTRIES: CatalogEntry[] = [
  {
    id: 'views404',
    title: 'Views404',
    get about() {
      return t('catalog.views404.about')
    },
    file: 'src/views/404.vue',
    component: Views404,
    // 空态块（BaseEmptyState）里那颗图标是 <v-icon>，文字走 t()。
    needs: UI_T,
    states: [
      {
        get name() {
          return t('catalog.views404.s1.name')
        },
        get note() {
          return t('catalog.views404.s1.note')
        },
        props: {},
        expect: '页面不存在',
      },
    ],
  },
  {
    id: 'docs-sign-in-view',
    title: 'DocsSignInView',
    get about() {
      return t('catalog.docsSignInView.about')
    },
    file: 'src/views/DocsSignInView.vue',
    component: DocsSignInView,
    needs: UI_T,
    args: { error: '', loading: false },
    states: [
      {
        get name() {
          return t('catalog.docsSignInView.s1.name')
        },
        get note() {
          return t('catalog.docsSignInView.s1.note')
        },
        props: {},
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.docsSignInView.s2.name')
        },
        get note() {
          return t('catalog.docsSignInView.s2.note')
        },
        props: { error: '文档站暂时不可用，请稍后再试', loading: false },
        expect: '文档站暂时不可用，请稍后再试',
        // 出错那一条是 v-alert；正常态没有，所以它只在出错时有。
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'add-email-view',
    title: 'AddEmailView',
    get about() {
      return t('catalog.addEmailView.about')
    },
    file: 'src/views/account/AddEmailView.vue',
    component: AddEmailView,
    needs: UI_T,
    args: { email: '', error: '', sending: false, signingOut: false, verify: ok, send: done },
    states: [
      {
        get name() {
          return t('catalog.addEmailView.s1.name')
        },
        get note() {
          return t('catalog.addEmailView.s1.note')
        },
        props: { step: 'email' },
        // 正常页（顶上没有那条出错 alert）：页头紧挨着表单，中间没插别的东西。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.addEmailView.s2.name')
        },
        get note() {
          return t('catalog.addEmailView.s2.note')
        },
        props: { step: 'email', email: 'taken@school.edu', error: '这个邮箱已被其他账号使用' },
        expect: '这个邮箱已被其他账号使用',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.addEmailView.s3.name')
        },
        get note() {
          return t('catalog.addEmailView.s3.note')
        },
        props: { step: 'code', email: 'wang@school.edu' },
        expect: '验证码已发送至 wang@school.edu',
        // 六个格子那一排只在这一步才有。
        expectSelector: '.account-otp',
      },
    ],
  },
  {
    id: 'app-sign-in-finish-view',
    title: 'AppSignInFinishView',
    get about() {
      return t('catalog.appSignInFinishView.about')
    },
    file: 'src/views/account/AppSignInFinishView.vue',
    component: AppSignInFinishView,
    needs: UI_T_R,
    args: { failed: false },
    states: [
      {
        get name() {
          return t('catalog.appSignInFinishView.s1.name')
        },
        get note() {
          return t('catalog.appSignInFinishView.s1.note')
        },
        props: {},
        expect: '正在登录',
        // 还在换：能看见的是那条进度线，出错那一档才换成提示和按钮。
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.appSignInFinishView.s2.name')
        },
        get note() {
          return t('catalog.appSignInFinishView.s2.note')
        },
        props: { failed: true },
        expect: '这次登录已失效，重新登录',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'app-sign-in-start-view',
    title: 'AppSignInStartView',
    get about() {
      return t('catalog.appSignInStartView.about')
    },
    file: 'src/views/account/AppSignInStartView.vue',
    component: AppSignInStartView,
    needs: ['vuetify'],
    states: [
      {
        get name() {
          return t('catalog.appSignInStartView.s1.name')
        },
        get note() {
          return t('catalog.appSignInStartView.s1.note')
        },
        props: {},
        expectSelector: '[role="progressbar"]',
      },
    ],
  },
  {
    id: 'back-to-app-view',
    title: 'BackToAppView',
    get about() {
      return t('catalog.backToAppView.about')
    },
    file: 'src/views/account/BackToAppView.vue',
    component: BackToAppView,
    needs: UI_T,
    args: { failed: false, link: '', asking: false, handing: false, account: BACK_TO_APP_ACCOUNT },
    states: [
      {
        get name() {
          return t('catalog.backToAppView.s1.name')
        },
        get note() {
          return t('catalog.backToAppView.s1.note')
        },
        props: {},
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.backToAppView.s2.name')
        },
        get note() {
          return t('catalog.backToAppView.s2.note')
        },
        props: { asking: true },
        expect: '登录知是 app',
        // 这一步那颗主按钮是「按钮」（点了报事件），不是给出去的链接。
        expectSelector: 'button.account-submit',
      },
      {
        get name() {
          return t('catalog.backToAppView.s3.name')
        },
        get note() {
          return t('catalog.backToAppView.s3.note')
        },
        props: { link: 'cheese://signin?grant=abc123' },
        expect: '回到知是 app',
        // 拿到链接时那颗主按钮带 href，画出来是 <a>（真链接能中键新开）。
        expectSelector: 'a.account-submit',
      },
      {
        get name() {
          return t('catalog.backToAppView.s4.name')
        },
        get note() {
          return t('catalog.backToAppView.s4.note')
        },
        props: { failed: true },
        expect: '无法把这次登录交给知是 app，在 app 中重新登录',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'desktop-sign-in-view',
    title: 'DesktopSignInView',
    get about() {
      return t('catalog.desktopSignInView.about')
    },
    file: 'src/views/account/DesktopSignInView.vue',
    component: DesktopSignInView,
    needs: UI_T,
    args: { entry: 'signin', opened: false, busy: false, errorMessage: '' },
    states: [
      {
        get name() {
          return t('catalog.desktopSignInView.s1.name')
        },
        get note() {
          return t('catalog.desktopSignInView.s1.note')
        },
        props: {},
        expect: '在浏览器中登录',
        // 正常态顶上没有提示条：页头紧挨着那颗主按钮。
        expectSelector: '.account-heading + .account-submit',
      },
      {
        get name() {
          return t('catalog.desktopSignInView.s2.name')
        },
        get note() {
          return t('catalog.desktopSignInView.s2.note')
        },
        props: { opened: true },
        expect: '已在浏览器中打开。完成后会回到这里。',
        // 打开过之后主按钮底下多出那颗「复制链接」，是按钮不是链接。
        expectSelector: '.account-submit + button',
      },
      {
        get name() {
          return t('catalog.desktopSignInView.s3.name')
        },
        get note() {
          return t('catalog.desktopSignInView.s3.note')
        },
        props: { opened: true, errorMessage: '无法打开默认浏览器，请手动复制链接' },
        expect: '无法打开默认浏览器，请手动复制链接',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.desktopSignInView.s4.name')
        },
        get note() {
          return t('catalog.desktopSignInView.s4.note')
        },
        props: { entry: 'signup' },
        expect: '注册知是',
      },
    ],
  },
  {
    id: 'o-auth-complete-view',
    title: 'OAuthCompleteView',
    get about() {
      return t('catalog.oAuthCompleteView.about')
    },
    file: 'src/views/account/OAuthCompleteView.vue',
    component: OAuthCompleteView,
    needs: UI_T_R,
    args: {
      loading: false,
      error: '',
      oauthState: OAUTH_STATE,
      providerName: '学校账号',
      step: 'form',
      requireInviteCode: false,
      registrationConfigReady: true,
      creating: false,
      binding: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
      verifyEmail: ok,
      sendCode: done,
    },
    states: [
      {
        get name() {
          return t('catalog.oAuthCompleteView.s1.name')
        },
        get note() {
          return t('catalog.oAuthCompleteView.s1.note')
        },
        props: { loading: true, oauthState: null },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.oAuthCompleteView.s2.name')
        },
        get note() {
          return t('catalog.oAuthCompleteView.s2.note')
        },
        props: {},
        // 默认停在「创建新账号」那一半，第一栏就是它自己的用户名框。
        expectSelector: '#oauth-create-username',
      },
      {
        get name() {
          return t('catalog.oAuthCompleteView.s3.name')
        },
        get note() {
          return t('catalog.oAuthCompleteView.s3.note')
        },
        props: { requireInviteCode: true },
        expect: '邀请码',
        // 要邀请码时表单里多出来的那一栏。
        expectSelector: '#oauth-create-invite',
      },
      {
        get name() {
          return t('catalog.oAuthCompleteView.s4.name')
        },
        get note() {
          return t('catalog.oAuthCompleteView.s4.note')
        },
        props: { oauthState: null, error: '这次登录已过期，请重新发起' },
        expect: '无法继续登录',
        // 读不到状态时页头直接接一颗回登录页的主按钮，没有表单可画。
        expectSelector: '.account-heading + .account-submit',
      },
    ],
  },
  {
    id: 'o-auth-error-view',
    title: 'OAuthErrorView',
    get about() {
      return t('catalog.oAuthErrorView.about')
    },
    file: 'src/views/account/OAuthErrorView.vue',
    component: OAuthErrorView,
    needs: UI_T_R,
    args: { errorDescription: '', providerId: '', providerName: '学校账号' },
    states: [
      {
        get name() {
          return t('catalog.oAuthErrorView.s1.name')
        },
        get note() {
          return t('catalog.oAuthErrorView.s1.note')
        },
        props: {},
        // 没有那条 alert 时，页头紧挨着按钮那一组。
        expectSelector: '.account-heading + .account-actions',
      },
      {
        get name() {
          return t('catalog.oAuthErrorView.s2.name')
        },
        get note() {
          return t('catalog.oAuthErrorView.s2.note')
        },
        props: { errorDescription: '你在提供方那里取消了授权' },
        expect: '你在提供方那里取消了授权',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.oAuthErrorView.s3.name')
        },
        get note() {
          return t('catalog.oAuthErrorView.s3.note')
        },
        props: { providerId: 'school-sso' },
        expect: '重新使用 学校账号 登录',
        // 回登录页那颗画成 <a>，重试那颗才是 <button>：有第二颗时才有它。
        expectSelector: '.account-actions button',
      },
    ],
  },
  {
    id: 'o-auth-success-view',
    title: 'OAuthSuccessView',
    get about() {
      return t('catalog.oAuthSuccessView.about')
    },
    file: 'src/views/account/OAuthSuccessView.vue',
    component: OAuthSuccessView,
    needs: UI_T_R,
    args: { processing: true, error: '', providerName: '学校账号' },
    states: [
      {
        get name() {
          return t('catalog.oAuthSuccessView.s1.name')
        },
        get note() {
          return t('catalog.oAuthSuccessView.s1.note')
        },
        props: {},
        expect: '正在完成 学校账号 登录',
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.oAuthSuccessView.s2.name')
        },
        get note() {
          return t('catalog.oAuthSuccessView.s2.note')
        },
        props: { processing: false },
        expect: '正在跳转',
      },
      {
        get name() {
          return t('catalog.oAuthSuccessView.s3.name')
        },
        get note() {
          return t('catalog.oAuthSuccessView.s3.note')
        },
        props: { error: '登录信息已失效，请重新登录' },
        expect: '登录信息已失效，请重新登录',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'o-auth-verify-view',
    title: 'OAuthVerifyView',
    get about() {
      return t('catalog.oAuthVerifyView.about')
    },
    file: 'src/views/account/OAuthVerifyView.vue',
    component: OAuthVerifyView,
    needs: UI_T_R,
    args: { email: 'wang@school.edu', error: '', errorMessage: '', loading: false },
    states: [
      {
        get name() {
          return t('catalog.oAuthVerifyView.s1.name')
        },
        get note() {
          return t('catalog.oAuthVerifyView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.oAuthVerifyView.s2.name')
        },
        get note() {
          return t('catalog.oAuthVerifyView.s2.note')
        },
        props: { errorMessage: '密码不正确' },
        expect: '密码不正确',
        // 那句话落在密码框上，框自己带出错态，顶上没有 alert。
        expectSelector: '.v-input--error',
      },
      {
        get name() {
          return t('catalog.oAuthVerifyView.s3.name')
        },
        get note() {
          return t('catalog.oAuthVerifyView.s3.note')
        },
        props: { error: '这个邮箱已经绑定了另一个账号' },
        expect: '这个邮箱已经绑定了另一个账号',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'passkey-offer-view',
    title: 'PasskeyOfferView',
    get about() {
      return t('catalog.passkeyOfferView.about')
    },
    file: 'src/views/account/PasskeyOfferView.vue',
    component: PasskeyOfferView,
    needs: UI_T,
    args: { offer: { canStopAsking: false }, adding: false, declining: null, errorMessage: '' },
    states: [
      {
        get name() {
          return t('catalog.passkeyOfferView.s1.name')
        },
        get note() {
          return t('catalog.passkeyOfferView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条错误提示：页头紧挨着按钮那一组。
        expectSelector: '.account-heading + .account-actions',
      },
      {
        get name() {
          return t('catalog.passkeyOfferView.s2.name')
        },
        get note() {
          return t('catalog.passkeyOfferView.s2.note')
        },
        props: { offer: { canStopAsking: true } },
        expect: '不再提醒',
        // 「不再提醒」是页脚里那颗安静的字，只有被拒过的账号才有。
        expectSelector: '.account-foot button',
      },
      {
        get name() {
          return t('catalog.passkeyOfferView.s3.name')
        },
        get note() {
          return t('catalog.passkeyOfferView.s3.note')
        },
        props: { errorMessage: '这台设备不支持通行密钥' },
        expect: '这台设备不支持通行密钥',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'sign-in-view',
    title: 'SignInView',
    get about() {
      return t('catalog.signInView.about')
    },
    file: 'src/views/account/SignInView.vue',
    component: SignInView,
    needs: UI_T_R,
    args: {
      errorMessage: '',
      notice: null,
      parts: ['alt', 'or', 'form'],
      alternatives: SIGN_IN_WAYS,
      last: null,
      busy: null,
      submitting: false,
      waiting: false,
      initialUsername: '',
    },
    states: [
      {
        get name() {
          return t('catalog.signInView.s1.name')
        },
        get note() {
          return t('catalog.signInView.s1.note')
        },
        props: {},
        // 正常态顶上没有提示条：页头紧挨着「一click的方式」那一组。
        expectSelector: '.account-heading + .signin-alt',
      },
      {
        get name() {
          return t('catalog.signInView.s2.name')
        },
        get note() {
          return t('catalog.signInView.s2.note')
        },
        props: { last: 'passkey' },
        expect: '上次使用',
        // 「上次使用」那枚小标只挂在被认出来的那一颗上。
        expectSelector: '.signin-alt__last',
      },
      {
        get name() {
          return t('catalog.signInView.s3.name')
        },
        get note() {
          return t('catalog.signInView.s3.note')
        },
        props: { errorMessage: '用户名或密码不正确，请 60 秒后再试' },
        expect: '用户名或密码不正确，请 60 秒后再试',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.signInView.s4.name')
        },
        get note() {
          return t('catalog.signInView.s4.note')
        },
        props: { notice: '密码已重置，请用新密码登录' },
        expect: '密码已重置，请用新密码登录',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'verify2-fa-view',
    title: 'Verify2FAView',
    get about() {
      return t('catalog.verify2FaView.about')
    },
    file: 'src/views/account/Verify2FAView.vue',
    component: Verify2FAView,
    needs: UI_T_R,
    // 用过备用码之后那颗提醒是 v-dialog，画在 body 上。
    teleport: true,
    args: {
      codeType: 'totp',
      errorMessage: '',
      loading: false,
      submitDisabled: true,
      trustDevice: false,
      totpCode: '',
      backupCode: '',
      backTo: '/account/signin',
      showBackupCodeDialog: false,
    },
    states: [
      {
        get name() {
          return t('catalog.verify2FaView.s1.name')
        },
        get note() {
          return t('catalog.verify2FaView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.verify2FaView.s2.name')
        },
        get note() {
          return t('catalog.verify2FaView.s2.note')
        },
        props: { codeType: 'backup' },
        expect: '输入一个 8 位备用码',
        // 备用码收的是字符（inputmode=text），验证器那一档收数字（numeric）。
        expectSelector: '.account-otp input[inputmode="text"]',
      },
      {
        get name() {
          return t('catalog.verify2FaView.s3.name')
        },
        get note() {
          return t('catalog.verify2FaView.s3.note')
        },
        props: { errorMessage: '验证码不正确' },
        expect: '验证码不正确',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.verify2FaView.s4.name')
        },
        get note() {
          return t('catalog.verify2FaView.s4.note')
        },
        props: { showBackupCodeDialog: true },
        expect: '每个备用码只能使用一次。建议现在生成一组新的备用码。',
        expectSelector: '[role="dialog"]',
      },
    ],
  },
  {
    id: 'request-view',
    title: 'RequestView',
    get about() {
      return t('catalog.requestView.about')
    },
    file: 'src/views/account/emailCode/RequestView.vue',
    component: RequestView,
    needs: UI_T_R,
    args: { initialEmail: '', error: '', waiting: false, submitting: false, backToSignIn: '/account/signin' },
    states: [
      {
        get name() {
          return t('catalog.requestView.s1.name')
        },
        get note() {
          return t('catalog.requestView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.requestView.s2.name')
        },
        get note() {
          return t('catalog.requestView.s2.note')
        },
        props: { error: '发送太频繁，请稍后再试' },
        expect: '发送太频繁，请稍后再试',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'email-code-verify-view',
    title: 'EmailCodeVerifyView',
    get about() {
      return t('catalog.emailCodeVerifyView.about')
    },
    file: 'src/views/account/emailCode/VerifyView.vue',
    component: EmailCodeVerifyView,
    needs: UI_T_R,
    args: {
      pending: EMAIL_PENDING,
      code: '',
      error: '',
      submitting: false,
      waiting: false,
      resending: false,
      resendWait: 0,
      backToSignIn: '/account/signin',
    },
    states: [
      {
        get name() {
          return t('catalog.emailCodeVerifyView.s1.name')
        },
        get note() {
          return t('catalog.emailCodeVerifyView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.emailCodeVerifyView.s2.name')
        },
        get note() {
          return t('catalog.emailCodeVerifyView.s2.note')
        },
        props: { resendWait: 42 },
        expect: '42 秒后可重新发送',
        // 倒计时那块只在刚发过一封时替换掉「重新发送」。
        expectSelector: '.account-foot__wait',
      },
      {
        get name() {
          return t('catalog.emailCodeVerifyView.s3.name')
        },
        get note() {
          return t('catalog.emailCodeVerifyView.s3.note')
        },
        props: { error: '验证码不正确，请重新输入' },
        expect: '验证码不正确，请重新输入',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'password-start-view',
    title: 'PasswordStartView',
    get about() {
      return t('catalog.passwordStartView.about')
    },
    file: 'src/views/account/recover/password/StartView.vue',
    component: PasswordStartView,
    needs: UI_T_R,
    args: { error: '', sent: false, submitting: false },
    states: [
      {
        get name() {
          return t('catalog.passwordStartView.s1.name')
        },
        get note() {
          return t('catalog.passwordStartView.s1.note')
        },
        props: {},
        expect: '发送重置邮件',
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.passwordStartView.s2.name')
        },
        get note() {
          return t('catalog.passwordStartView.s2.note')
        },
        props: { sent: true },
        expect: '查收邮件',
        // 发出之后表单让位：页头下面直接就是那颗回登录页的主按钮。
        expectSelector: '.account-heading + .account-submit',
      },
      {
        get name() {
          return t('catalog.passwordStartView.s3.name')
        },
        get note() {
          return t('catalog.passwordStartView.s3.note')
        },
        props: { error: '发送失败，请稍后再试' },
        expect: '发送失败，请稍后再试',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'password-verify-view',
    title: 'PasswordVerifyView',
    get about() {
      return t('catalog.passwordVerifyView.about')
    },
    file: 'src/views/account/recover/password/VerifyView.vue',
    component: PasswordVerifyView,
    needs: UI_T_R,
    args: { username: 'wangxiaoming', error: '', submitting: false },
    states: [
      {
        get name() {
          return t('catalog.passwordVerifyView.s1.name')
        },
        get note() {
          return t('catalog.passwordVerifyView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.passwordVerifyView.s2.name')
        },
        get note() {
          return t('catalog.passwordVerifyView.s2.note')
        },
        props: { error: '这条重置链接已失效，请重新申请' },
        expect: '这条重置链接已失效，请重新申请',
        expectSelector: '[role="alert"]',
      },
    ],
  },
  {
    id: 'signup-start-view',
    title: 'SignupStartView',
    get about() {
      return t('catalog.signupStartView.about')
    },
    file: 'src/views/account/signup/StartView.vue',
    component: SignupStartView,
    needs: UI_T_R,
    args: {
      error: '',
      requireInviteCode: false,
      registrationConfigReady: true,
      submitting: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
    },
    states: [
      {
        get name() {
          return t('catalog.signupStartView.s1.name')
        },
        get note() {
          return t('catalog.signupStartView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.signupStartView.s2.name')
        },
        get note() {
          return t('catalog.signupStartView.s2.note')
        },
        props: { requireInviteCode: true },
        expect: '邀请码',
        // 要邀请码时表单里才有的那一栏。
        expectSelector: '#signup-invite-code',
      },
      {
        get name() {
          return t('catalog.signupStartView.s3.name')
        },
        get note() {
          return t('catalog.signupStartView.s3.note')
        },
        props: { error: '这个用户名已被占用' },
        expect: '这个用户名已被占用',
        expectSelector: '[role="alert"]',
      },
      {
        get name() {
          return t('catalog.signupStartView.s4.name')
        },
        get note() {
          return t('catalog.signupStartView.s4.note')
        },
        props: { registrationConfigReady: false, consentLoadError: '暂时无法获取协议，刷新页面重试' },
        expect: '暂时无法获取协议，刷新页面重试',
        // 配置没齐：主按钮画成按下不动的样子。
        expectSelector: '.account-submit.v-btn--disabled',
      },
    ],
  },
  {
    id: 'verify-email-view',
    title: 'VerifyEmailView',
    get about() {
      return t('catalog.verifyEmailView.about')
    },
    file: 'src/views/account/signup/VerifyEmailView.vue',
    component: VerifyEmailView,
    needs: UI_T_R,
    args: {
      email: 'wang@school.edu',
      error: '',
      needsPassword: false,
      needsConsent: false,
      waiting: false,
      submitting: false,
      resendWait: 0,
      resending: false,
      consentDocuments: CONSENT_DOCUMENTS,
      consentLoadError: '',
    },
    states: [
      {
        get name() {
          return t('catalog.verifyEmailView.s1.name')
        },
        get note() {
          return t('catalog.verifyEmailView.s1.note')
        },
        props: {},
        // 正常态顶上没有那条 alert：页头紧挨着表单。
        expectSelector: '.account-heading + form',
      },
      {
        get name() {
          return t('catalog.verifyEmailView.s2.name')
        },
        get note() {
          return t('catalog.verifyEmailView.s2.note')
        },
        props: { needsPassword: true },
        expect: '再次输入注册时设置的密码',
        // 只在刷新丢掉状态时才多出来的那一栏密码。
        expectSelector: '#verify-password',
      },
      {
        get name() {
          return t('catalog.verifyEmailView.s3.name')
        },
        get note() {
          return t('catalog.verifyEmailView.s3.note')
        },
        props: { resendWait: 30 },
        expect: '30 秒后可重新发送',
        // 倒计时那块只在刚发过一封时替换掉「重新发送」。
        expectSelector: '.account-foot__wait',
      },
      {
        get name() {
          return t('catalog.verifyEmailView.s4.name')
        },
        get note() {
          return t('catalog.verifyEmailView.s4.note')
        },
        props: { error: '验证码不正确，请重新输入' },
        expect: '验证码不正确，请重新输入',
        expectSelector: '[role="alert"]',
      },
    ],
  },
]
