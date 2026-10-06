// 团队邀请在「待办」的动态里就能接受或拒绝，不用再绕到 团队 →「待定」。
// 通知的形状照后端 /notifications 真实返回的写：邀请是 entities.application，带它此刻的状态。
import type { Notification } from '@/network/api/notifications/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import i18n, { setLocale } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'

vi.mock('@/network/api/teams', () => ({
  TeamsApi: { acceptInvitation: vi.fn(), declineInvitation: vi.fn() },
}))

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(TeamsApi.acceptInvitation)
    .mockReset()
    .mockResolvedValue(undefined as never)
  vi.mocked(TeamsApi.declineInvitation)
    .mockReset()
    .mockResolvedValue(undefined as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// 每个用例用自己的邀请 id：答过的邀请记在组件之外，跨用例还在。
function invitation(status: string, id: string, read = false): Notification {
  return {
    id: 9,
    type: 'TEAM_INVITATION',
    read,
    createdAt: Date.now(),
    entities: {
      inviter: { id: '1', type: 'user', name: '林夏', handle: 'linxia' },
      team: { id: '5', type: 'team', name: '数据组', url: '/teams/data' },
      application: { id, type: 'team_membership_application', name: '', status },
      invitedUser: { id: '2', type: 'user', name: '我', handle: 'me' },
    },
    contextMetadata: { role: 'MEMBER', message: '' },
  }
}

async function mount(notification: Notification) {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/teams/:handle/members', name: 'TeamsDetailMembers', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  const onMarkAsRead = vi.fn()
  const view = render(NotificationItem, {
    props: { notification, onMarkAsRead, onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  return { view, onMarkAsRead }
}

it('accepts a waiting invitation right from the notification', async () => {
  const { view, onMarkAsRead } = await mount(invitation('PENDING', '42', true))

  const accept = await view.findByRole('button', { name: '接受' })
  await fireEvent.click(accept)

  await waitFor(() => expect(TeamsApi.acceptInvitation).toHaveBeenCalledWith(42))
  await waitFor(() => expect(onMarkAsRead).toHaveBeenCalledWith(9))
  // 答过的不再给人答第二次（这条本来就已读，标已读不会让行重画）。
  await waitFor(() => expect(view.queryByRole('button', { name: '接受' })).toBeNull())
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})

it('a second click while the answer is on its way sends nothing more', async () => {
  let settle!: () => void
  vi.mocked(TeamsApi.acceptInvitation).mockImplementation(
    () => new Promise((resolve) => (settle = () => resolve(undefined as never)))
  )
  const { view } = await mount(invitation('PENDING', '43'))

  const accept = await view.findByRole('button', { name: '接受' })
  await fireEvent.click(accept)
  await fireEvent.click(accept)
  await fireEvent.click(view.getByRole('button', { name: '拒绝' }))
  settle()

  await waitFor(() => expect(view.queryByRole('button', { name: '接受' })).toBeNull())
  expect(TeamsApi.acceptInvitation).toHaveBeenCalledTimes(1)
  expect(TeamsApi.declineInvitation).not.toHaveBeenCalled()
})

it('declines a waiting invitation right from the notification', async () => {
  const { view } = await mount(invitation('PENDING', '44'))

  await fireEvent.click(await view.findByRole('button', { name: '拒绝' }))

  await waitFor(() => expect(TeamsApi.declineInvitation).toHaveBeenCalledWith(44))
  expect(TeamsApi.acceptInvitation).not.toHaveBeenCalled()
})

it('offers no answer once the invitation has been answered', async () => {
  const { view } = await mount(invitation('ACCEPTED', '45'))

  // 通用动作出现说明内容已经交上来了，此时不该再有接受 / 拒绝。
  await view.findByRole('button', { name: '删除' })
  expect(view.queryByRole('button', { name: '接受' })).toBeNull()
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})
