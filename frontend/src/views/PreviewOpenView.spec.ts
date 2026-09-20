import { reactive } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const requestPreviewSession = vi.fn()
const route = reactive({
  params: { topicId: 'topic-a' },
  query: {} as Record<string, string>,
  fullPath: '/previews/topic-a',
})
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
}))
vi.mock('vue-router', () => ({ useRoute: () => route }))

import { ApiError } from '../api'

import PreviewOpenView from './PreviewOpenView.vue'

function mount() {
  return render(PreviewOpenView, { global: { plugins: [createVuetify({ components, directives })] } })
}

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  })
  route.params.topicId = 'topic-a'
  route.query = {}
  route.fullPath = '/previews/topic-a'
  requestPreviewSession.mockReset()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('requires platform login before requesting a preview grant', async () => {
  mount()
  expect(await screen.findByText('这个预览仅项目成员可访问，请先登录')).toBeTruthy()
  expect(requestPreviewSession).not.toHaveBeenCalled()
})

it('posts only a scoped preview grant and requested path to the content host', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  route.query.path = '/report/page?view=detail'
  route.fullPath += '?path=%2Freport%2Fpage%3Fview%3Ddetail'
  requestPreviewSession.mockResolvedValue({ url: 'https://preview-a.example/_cheese/session', grant: 'preview-grant' })
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
      action: 'https://preview-a.example/_cheese/session',
      method: 'post',
      body: 'grant=preview-grant&path=%2Freport%2Fpage%3Fview%3Ddetail',
    })
  )
  expect(requestPreviewSession).toHaveBeenCalledWith('topic-a')
  expect(document.querySelector('form')).toBeNull()
  expect(localStorage.getItem('accessToken')).toBe('platform-secret')
})

it('shows launch failures and offers retry', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestPreviewSession.mockRejectedValue(new Error('预览暂不可用'))
  mount()
  expect(await screen.findByText('预览暂不可用')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
})

it('returns to login when authorization expires', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  requestPreviewSession.mockRejectedValue(new ApiError(401, '需要登录'))
  mount()
  expect(await screen.findByText('这个预览仅项目成员可访问，请先登录')).toBeTruthy()
})

it('does not submit a stale grant after navigating to another topic', async () => {
  localStorage.setItem('accessToken', 'platform-secret')
  let finish: (value: { url: string; grant: string }) => void = () => {}
  requestPreviewSession.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  requestPreviewSession.mockRejectedValue(new Error('预览暂不可用'))
  const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {})
  mount()
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledWith('topic-a'))
  route.params.topicId = 'topic-b'
  route.fullPath = '/previews/topic-b'
  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalledWith('topic-b'))
  finish({ url: 'https://preview-a.example/_cheese/session', grant: 'stale-grant' })
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(submit).not.toHaveBeenCalled()
})
