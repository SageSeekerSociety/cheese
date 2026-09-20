import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const requestSiteSession = vi.fn()
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  requestSiteSession: (...args: unknown[]) => requestSiteSession(...args),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { projectId: 'project-a' }, query: {}, fullPath: '/sites/project-a' }),
}))

import SiteOpenView from './SiteOpenView.vue'

function mount() {
  return render(SiteOpenView, { global: { plugins: [createVuetify({ components, directives })] } })
}

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  })
  requestSiteSession.mockReset()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('requires login before asking for a Site grant', async () => {
  mount()
  expect(await screen.findByText('这个网站仅项目成员可访问，请先登录')).toBeTruthy()
  expect(requestSiteSession).not.toHaveBeenCalled()
})

it('posts only the Site grant to the content host', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestSiteSession.mockResolvedValue({ url: 'https://site.example/_cheese/session', grant: 'site-read-grant' })
  let submitted: { action: string; method: string; body: string } | undefined
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    submitted = {
      action: this.action,
      method: this.method.toLowerCase(),
      body: new URLSearchParams(new FormData(this) as never).toString(),
    }
  })
  mount()
  await waitFor(() =>
    expect(submitted).toEqual({
      action: 'https://site.example/_cheese/session',
      method: 'post',
      body: 'grant=site-read-grant',
    })
  )
  expect(requestSiteSession).toHaveBeenCalledWith('project-a')
  expect(document.querySelector('form')).toBeNull()
})

it('shows a failed launch instead of navigating to an empty website', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestSiteSession.mockRejectedValue(new Error('暂无已发布的网站'))
  mount()
  expect(await screen.findByText('暂无已发布的网站')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
})
