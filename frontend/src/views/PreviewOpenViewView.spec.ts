import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewOpenViewView from './PreviewOpenViewView.vue'

import { setLocale } from '@/i18n'

type Props = { loading: boolean; error: string; needsLogin: boolean }

// Mounts the view from props alone — no router, no store, no api. The three
// booleans are the whole input; the container `PreviewOpenView.vue` owns the rest.
function mount(props: Props) {
  return render(PreviewOpenViewView, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

afterEach(() => {
  cleanup()
})

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

it('shows the members-only hint while the viewer is signed out', () => {
  mount({ loading: false, error: '', needsLogin: true })
  expect(screen.getByText('这个预览仅项目成员可访问，请先登录')).toBeTruthy()
  expect(screen.getByText('登录')).toBeTruthy()
})

it('spins while the session is in flight', () => {
  mount({ loading: true, error: '', needsLogin: false })
  expect(screen.getByLabelText('正在打开预览')).toBeTruthy()
})

it('shows the failure and emits retry when opening failed', async () => {
  const { emitted } = mount({ loading: false, error: '预览打开失败', needsLogin: false })
  expect(screen.getByText('预览打开失败')).toBeTruthy()
  await fireEvent.click(screen.getByRole('button', { name: '重试' }))
  expect(emitted().retry).toHaveLength(1)
})

it('reads in English under the en locale', () => {
  setLocale('en')
  mount({ loading: false, error: '', needsLogin: true })
  expect(screen.getByText('Only project members can open this preview. Sign in first')).toBeTruthy()
})
