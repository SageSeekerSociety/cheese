import type { DeviceApproval } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import ConnectViewView from './ConnectViewView.vue'

import i18n, { setLocale } from '@/i18n'

type Props = {
  code: string
  loggedIn: boolean
  loading: boolean
  error: string | null
  approved: DeviceApproval | null
  proposedName: string
}

const base: Props = {
  code: '',
  loggedIn: false,
  loading: false,
  error: null,
  approved: null,
  proposedName: '',
}

// Mounts the view from props alone — the container `ConnectView.vue` owns the
// route, the api and the approve call; this half only draws what it is handed.
function mount(props: Partial<Props> = {}) {
  return render(ConnectViewView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

afterEach(() => {
  cleanup()
})

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

it('asks the cli link for a code when the address carries none', () => {
  mount({ code: '' })
  expect(screen.getByText(/链接里没有设备码/)).toBeTruthy()
})

it('sends a signed-out visitor to sign in before approving', () => {
  mount({ code: 'WXYZ-1234', loggedIn: false })
  expect(screen.getByText('请先登录，再批准这台设备归你所有')).toBeTruthy()
  expect(screen.getByText('去登录')).toBeTruthy()
})

it('shows the code and prefills the cli-proposed device name', () => {
  mount({ code: 'WXYZ-1234', loggedIn: true, proposedName: 'andy-macbook' })
  expect(screen.getByText('WXYZ-1234')).toBeTruthy()
  expect(screen.getByDisplayValue('andy-macbook')).toBeTruthy()
})

it('approves with the name shown in the field', async () => {
  const { emitted } = mount({ code: 'WXYZ-1234', loggedIn: true, proposedName: 'andy-macbook' })
  await fireEvent.click(screen.getByRole('button', { name: '批准并绑定到我' }))
  expect(emitted().approve).toEqual([['andy-macbook']])
})

it('approves the edited name, not the proposed one', async () => {
  const { emitted } = mount({ code: 'WXYZ-1234', loggedIn: true, proposedName: 'andy-macbook' })
  await fireEvent.update(screen.getByRole('textbox'), 'renamed-node')
  await fireEvent.click(screen.getByRole('button', { name: '批准并绑定到我' }))
  expect(emitted().approve).toEqual([['renamed-node']])
})

it('shows the approval error in place', () => {
  mount({ code: 'WXYZ-1234', loggedIn: true, error: '审批失败' })
  expect(screen.getByText('审批失败')).toBeTruthy()
})

it('shows the bound device once approval lands', () => {
  mount({
    code: 'WXYZ-1234',
    loggedIn: true,
    approved: { device_id: 'dev-1', device_name: 'andy-macbook', project_id: null },
  })
  expect(screen.getByText('设备已连接')).toBeTruthy()
  expect(screen.getByText('andy-macbook')).toBeTruthy()
})

it('reads in English under the en locale', () => {
  setLocale('en')
  mount({ code: '' })
  expect(screen.getByText(/The link has no device code/)).toBeTruthy()
})
