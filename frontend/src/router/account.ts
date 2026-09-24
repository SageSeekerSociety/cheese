import type { RouteRecordRaw } from 'vue-router'

export default {
  path: '/account',
  name: 'Account',
  component: () => import('@/layouts/account/Account.vue'),
  meta: {
    hideAppBar: true,
  },
  children: [
    {
      path: 'signin',
      name: 'SignIn',
      component: () => import('@/views/account/SignIn.vue'),
      meta: {
        title: '登录',
        titleKey: 'account.signIn.title',
      },
    },
    {
      path: 'signup',
      name: 'SignUpStart',
      component: () => import('@/views/account/signup/Start.vue'),
      meta: {
        title: '创建账号',
        titleKey: 'account.signUp.title',
      },
    },
    {
      path: 'signup/verify-email',
      name: 'SignUpVerifyEmail',
      component: () => import('@/views/account/signup/VerifyEmail.vue'),
      meta: {
        title: '验证邮箱',
        titleKey: 'account.verifyEmail.title',
      },
    },
    {
      path: 'recover/password',
      name: 'RecoverPasswordRequest',
      component: () => import('@/views/account/recover/password/Start.vue'),
      meta: {
        title: '找回密码',
        titleKey: 'account.recover.title',
      },
    },
    {
      path: 'recover/password/verify',
      name: 'RecoverPasswordVerify',
      component: () => import('@/views/account/recover/password/Verify.vue'),
      meta: {
        title: '设置新密码',
        titleKey: 'account.resetPassword.title',
      },
      beforeEnter: (to: any, from: any, next: any) => {
        if (!to.query.token) {
          next({ name: 'RecoverPasswordRequest' })
        } else {
          next()
        }
      },
    },
    {
      // Right after a password sign-in, still on the way in: the brand scene
      // stays, so arriving here from the sign-in page does not redraw it.
      path: 'passkey',
      name: 'PasskeyOffer',
      component: () => import('@/views/account/PasskeyOffer.vue'),
      meta: {
        title: '添加通行密钥',
        titleKey: 'account.passkeyOffer.add',
      },
    },
    {
      // Required of an account without an address of its own before it goes
      // anywhere else (router/emailRequired.ts).
      path: 'add-email',
      name: 'AccountAddEmail',
      component: () => import('@/views/account/AddEmail.vue'),
      meta: {
        title: '添加邮箱',
        titleKey: 'account.addEmail.title',
      },
    },
    {
      path: 'verify-2fa',
      name: 'Verify2FA',
      component: () => import('@/views/account/Verify2FA.vue'),
      meta: {
        title: '两步验证',
        titleKey: 'account.twoFactor.title',
      },
    },
    {
      path: 'oauth/complete',
      name: 'OAuthComplete',
      component: () => import('@/views/account/OAuthComplete.vue'),
      meta: {
        title: 'OAuth 账户选择',
      },
    },
    {
      path: 'oauth/verify',
      name: 'OAuthVerify',
      component: () => import('@/views/account/OAuthVerify.vue'),
      meta: {
        title: '关联账号',
        titleKey: 'account.oauth.verify.title',
      },
    },
    {
      path: 'oauth/success',
      name: 'OAuthSuccess',
      component: () => import('@/views/account/OAuthSuccess.vue'),
      meta: {
        title: '登录',
        titleKey: 'account.signIn.title',
      },
    },
    {
      path: 'oauth/error',
      name: 'OAuthError',
      component: () => import('@/views/account/OAuthError.vue'),
      meta: {
        title: '登录失败',
        titleKey: 'account.oauth.error.title',
      },
    },
  ],
} as RouteRecordRaw
