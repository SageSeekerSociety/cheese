/**
 * 预览站里「账号那一组整页」的前一半：404 兜底页、文档站登录页、补邮箱、几条 App 内
 * 的登录前后屏，以及 OAuth 那几屏（完成 / 出错 / 成功 / 验证）。
 *
 * 和 `catalogViewAccountPartB.ts` 一起构成这一册，由 `catalogViewAccount.ts` 合并成
 * `VIEW_ACCOUNT_ENTRIES` 交给 `catalogViews.ts`。各屏公用数据见 `catalogViewAccountFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { BACK_TO_APP_ACCOUNT, CONSENT_DOCUMENTS, OAUTH_STATE } from './catalogViewAccountFixtures'

import { t } from '@/i18n'
import Views404 from '@/views/404.vue'
import AddEmailView from '@/views/account/AddEmailView.vue'
import AppSignInFinishView from '@/views/account/AppSignInFinishView.vue'
import AppSignInStartView from '@/views/account/AppSignInStartView.vue'
import BackToAppView from '@/views/account/BackToAppView.vue'
import DesktopSignInView from '@/views/account/DesktopSignInView.vue'
import OAuthCompleteView from '@/views/account/OAuthCompleteView.vue'
import OAuthErrorView from '@/views/account/OAuthErrorView.vue'
import OAuthSuccessView from '@/views/account/OAuthSuccessView.vue'
import OAuthVerifyView from '@/views/account/OAuthVerifyView.vue'
import DocsSignInView from '@/views/DocsSignInView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面两样，再加一条 name 指得上的路由（按钮的 `to`、链接的去处按名字解析）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 页面里「验证码对不对」「再发一封」两趟接口由容器给，预览站上走一遍就完。 */
const ok = async () => {}
const done = async () => undefined

export const VIEW_ACCOUNT_ENTRIES_A: CatalogEntry[] = [
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
]
