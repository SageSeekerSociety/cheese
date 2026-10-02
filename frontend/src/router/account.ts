import type { RouteRecordRaw } from 'vue-router'

import { signInHappensInBrowser } from '@/views/account/appSignIn'

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
        titleKey: 'account.signIn.title',
      },
      beforeEnter: signInHappensInBrowser,
    },
    {
      path: 'signin/email',
      name: 'SignInEmailCode',
      component: () => import('@/views/account/emailCode/Request.vue'),
      meta: {
        titleKey: 'account.emailCode.title',
      },
      beforeEnter: signInHappensInBrowser,
    },
    {
      path: 'signin/email/verify',
      name: 'SignInEmailCodeVerify',
      component: () => import('@/views/account/emailCode/Verify.vue'),
      meta: {
        titleKey: 'account.emailCode.codeTitle',
      },
    },
    {
      path: 'signup',
      name: 'SignUpStart',
      component: () => import('@/views/account/signup/Start.vue'),
      meta: {
        titleKey: 'account.signUp.title',
      },
      beforeEnter: signInHappensInBrowser,
    },
    {
      path: 'signup/verify-email',
      name: 'SignUpVerifyEmail',
      component: () => import('@/views/account/signup/VerifyEmail.vue'),
      meta: {
        titleKey: 'account.verifyEmail.title',
      },
    },
    {
      path: 'recover/password',
      name: 'RecoverPasswordRequest',
      component: () => import('@/views/account/recover/password/Start.vue'),
      meta: {
        titleKey: 'account.recover.title',
      },
      beforeEnter: signInHappensInBrowser,
    },
    {
      path: 'recover/password/verify',
      name: 'RecoverPasswordVerify',
      component: () => import('@/views/account/recover/password/Verify.vue'),
      meta: {
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
        titleKey: 'account.addEmail.title',
      },
    },
    {
      path: 'verify-2fa',
      name: 'Verify2FA',
      component: () => import('@/views/account/Verify2FA.vue'),
      meta: {
        titleKey: 'account.twoFactor.title',
      },
    },
    {
      path: 'oauth/complete',
      name: 'OAuthComplete',
      component: () => import('@/views/account/OAuthComplete.vue'),
      meta: {
        titleKey: 'account.oauth.complete.pageTitle',
      },
    },
    {
      path: 'oauth/verify',
      name: 'OAuthVerify',
      component: () => import('@/views/account/OAuthVerify.vue'),
      meta: {
        titleKey: 'account.oauth.verify.title',
      },
    },
    {
      path: 'oauth/success',
      name: 'OAuthSuccess',
      component: () => import('@/views/account/OAuthSuccess.vue'),
      meta: {
        titleKey: 'account.signIn.title',
      },
    },
    // Signing in to the desktop app (views/account/appSignIn.ts): started in
    // the app, done and handed back in the browser, finished in the app.
    {
      path: 'app',
      name: 'DesktopSignIn',
      component: () => import('@/views/account/DesktopSignIn.vue'),
      meta: {
        titleKey: 'account.signIn.title',
      },
    },
    {
      path: 'oauth/app',
      name: 'AppSignInStart',
      component: () => import('@/views/account/AppSignInStart.vue'),
      meta: {
        titleKey: 'account.signIn.title',
      },
    },
    {
      path: 'oauth/to-app',
      name: 'AppSignInHandOff',
      component: () => import('@/views/account/BackToApp.vue'),
      props: { signIn: true },
      meta: {
        titleKey: 'account.signIn.title',
      },
    },
    {
      path: 'oauth/from-browser',
      name: 'AppSignInFinish',
      component: () => import('@/views/account/AppSignInFinish.vue'),
      meta: {
        titleKey: 'account.signIn.title',
      },
    },
    {
      // The result of an authorization the desktop app sent to the browser
      // (backend/app/api/app_return.py), shown in the app.
      path: 'to-app',
      name: 'BackToApp',
      component: () => import('@/views/account/BackToApp.vue'),
      meta: {
        title: 'Cheese',
      },
    },
    {
      path: 'oauth/error',
      name: 'OAuthError',
      component: () => import('@/views/account/OAuthError.vue'),
      meta: {
        titleKey: 'account.oauth.error.title',
      },
    },
  ],
} as RouteRecordRaw
