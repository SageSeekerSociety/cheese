import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const requestDocsGrant = vi.fn()
vi.mock('../api/docs', () => ({ requestDocsGrant: (...args: unknown[]) => requestDocsGrant(...args) }))
const replace = vi.fn()
const route = {
  query: { path: '/dev/turn#address' } as Record<string, string>,
  fullPath: '/docs-signin?path=%2Fdev%2Fturn%23address',
}
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace }) }))

import DocsSignInView from './DocsSignInView.vue'

function mount() {
  return render(DocsSignInView, { global: { plugins: [createVuetify({ components, directives })] } })
}

let submitted: { action: string; method: string; body: string } | undefined
beforeEach(() => {
  setLocale('zh-CN')
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  })
  requestDocsGrant.mockReset()
  replace.mockReset()
  submitted = undefined
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    submitted = {
      action: this.action,
      method: this.method.toLowerCase(),
      body: new URLSearchParams(new FormData(this) as never).toString(),
    }
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('signs in first, and comes back here afterwards', async () => {
  mount()
  await waitFor(() => expect(replace).toHaveBeenCalledWith({ name: 'SignIn', query: { redirect: route.fullPath } }))
  expect(requestDocsGrant).not.toHaveBeenCalled()
})

it('posts the grant and the page to return to, and nothing else, to the docs host', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestDocsGrant.mockResolvedValue({ url: 'https://docs.example.test/api/docs/session', grant: 'docs-grant' })
  mount()
  await waitFor(() =>
    expect(submitted).toEqual({
      action: 'https://docs.example.test/api/docs/session',
      method: 'post',
      body: 'grant=docs-grant&path=%2Fdev%2Fturn%23address',
    })
  )
  // The platform's own token never leaves for the docs host.
  expect(submitted?.body).not.toContain('platform-secret')
  expect(document.querySelector('form')).toBeNull()
})

it('a sign-in the platform no longer accepts goes to sign in again', async () => {
  localStorage.setItem('accessToken', 'expired')
  const { ApiError } = await import('../api')
  requestDocsGrant.mockRejectedValue(new ApiError(401, 'Login required'))
  mount()
  await waitFor(() => expect(replace).toHaveBeenCalledWith({ name: 'SignIn', query: { redirect: route.fullPath } }))
  expect(submitted).toBeUndefined()
})

it('says so when the grant cannot be had, and offers to try again', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestDocsGrant.mockRejectedValue(new Error('服务暂时不可用'))
  mount()
  expect(await screen.findByText('服务暂时不可用')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  expect(submitted).toBeUndefined()
})
