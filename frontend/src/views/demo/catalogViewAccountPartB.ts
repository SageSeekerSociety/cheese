/**
 * 预览站里「账号那一组整页」的后一半：passkey、登录、两步验证、邮箱验证码、改密码、
 * 注册与验证邮箱那几屏。
 *
 * 和 `catalogViewAccountPartA.ts` 一起构成这一册，由 `catalogViewAccount.ts` 合并成
 * `VIEW_ACCOUNT_ENTRIES`。各屏公用数据见 `catalogViewAccountFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { CONSENT_DOCUMENTS, EMAIL_PENDING, SIGN_IN_WAYS } from './catalogViewAccountFixtures'

import { t } from '@/i18n'
import RequestView from '@/views/account/emailCode/RequestView.vue'
import EmailCodeVerifyView from '@/views/account/emailCode/VerifyView.vue'
import PasskeyOfferView from '@/views/account/PasskeyOfferView.vue'
import PasswordStartView from '@/views/account/recover/password/StartView.vue'
import PasswordVerifyView from '@/views/account/recover/password/VerifyView.vue'
import SignInView from '@/views/account/SignInView.vue'
import SignupStartView from '@/views/account/signup/StartView.vue'
import VerifyEmailView from '@/views/account/signup/VerifyEmailView.vue'
import Verify2FAView from '@/views/account/Verify2FAView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 上面两样，再加一条 name 指得上的路由（按钮的 `to`、链接的去处按名字解析）。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

export const VIEW_ACCOUNT_ENTRIES_B: CatalogEntry[] = [
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
