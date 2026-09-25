// 没有自己邮箱的账号：去哪儿都先去补邮箱，补完回到要去的地方。
import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { requireEmail } from './emailRequired'

const blank = { template: '<div />' }

function setup(account: { loggedIn: boolean; user: { emailMissing?: boolean } | null }) {
  const signedIn = reactive({ ...account, sessionRestored: Promise.resolve() })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'Home', component: blank },
      { path: '/projects/:id', name: 'project', component: blank },
      { path: '/account/add-email', name: 'AccountAddEmail', component: blank },
      { path: '/account/signin', name: 'SignIn', component: blank },
      { path: '/account/oauth/success', name: 'OAuthSuccess', component: blank },
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
    ],
  })
  requireEmail(router, async () => signedIn)
  return { router, signedIn }
}

describe('an account without an email of its own', () => {
  it('is taken to add one first, carrying where it was going', async () => {
    const { router } = setup({ loggedIn: true, user: { emailMissing: true } })

    await router.push('/projects/7?tab=files')

    expect(router.currentRoute.value.name).toBe('AccountAddEmail')
    expect(router.currentRoute.value.query.redirect).toBe('/projects/7?tab=files')
  })

  it('goes on once the address is added', async () => {
    const { router, signedIn } = setup({ loggedIn: true, user: { emailMissing: true } })
    await router.push('/projects/7')

    signedIn.user = { emailMissing: false }
    await router.push('/projects/7')

    expect(router.currentRoute.value.fullPath).toBe('/projects/7')
  })

  it('is taken there when the server says so only after the page has opened', async () => {
    const { router, signedIn } = setup({ loggedIn: true, user: { emailMissing: false } })
    await router.push('/projects/7')

    signedIn.user = { emailMissing: true }

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('AccountAddEmail'))
    expect(router.currentRoute.value.query.redirect).toBe('/projects/7')
  })

  it('finishes signing in and reads the terms without being stopped', async () => {
    const { router } = setup({ loggedIn: true, user: { emailMissing: true } })

    await router.push('/account/oauth/success')
    expect(router.currentRoute.value.name).toBe('OAuthSuccess')

    await router.push('/legal/terms')
    expect(router.currentRoute.value.name).toBe('LegalTerms')
  })
})

describe('everyone else', () => {
  it('is not stopped', async () => {
    const { router } = setup({ loggedIn: true, user: { emailMissing: false } })

    await router.push('/projects/7')

    expect(router.currentRoute.value.fullPath).toBe('/projects/7')
  })

  it('is sent to sign in from the add-email screen when nobody is signed in', async () => {
    const { router } = setup({ loggedIn: false, user: null })

    await router.push('/account/add-email')

    expect(router.currentRoute.value.name).toBe('SignIn')
  })
})
