// 项目邀请在「待办」的动态里就能接受或拒绝。行本身不取数：答那一下的请求在容器那份桥
// （`useNotificationAnswers`）上，宿主把它递给这一行的渲染件。
import type { Notification } from '@/types/apiNotifications'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import { useNotificationAnswers } from '@/views/home/useNotificationAnswers'

import i18n, { setLocale } from '@/i18n'

const respondToInvitation = vi.fn()
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  respondToInvitation: (...args: unknown[]) => respondToInvitation(...args),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 宿主那份桥：答过的邀请记在它模块作用域里（跨行重挂还在），这里直接取一份接上。
const { answering } = useNotificationAnswers()

beforeAll(() => setLocale('zh-CN'))
// 块体：箭头体返回的是 mock 本身，而 vi.fn() 是可 then 的，钩子会 await 它、永远等下去。
beforeEach(() => {
  respondToInvitation.mockReset()
})

function invitation(extra: Record<string, string> = {}): Notification {
  return {
    id: 7,
    type: 'PROJECT_INVITE',
    read: false,
    createdAt: 0,
    entities: {
      project: { id: 'project-1', type: 'project', name: '样例项目' },
    },
    contextMetadata: { invitationId: 'invitation-1', projectName: '样例项目', ...extra },
  }
}

function show(notification: Notification, onMarkAsRead = vi.fn()) {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/teams/pending', name: 'HomeTeamsPending', component: Blank },
      { path: '/projects/:projectId', name: 'workspace-project', component: Blank },
    ],
  })
  return render(NotificationItem, {
    props: { notification, answering, onMarkAsRead, onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
}

// An open invitation links to the pending page. `NotificationItem` first renders the
// row without a link, learns from the render that it has one, and remounts the row
// with it; the buttons it shows are re-read from the new render on the next tick.
// Wait for both, as a person's click always does.
async function openRow(view: ReturnType<typeof show>) {
  await waitFor(() => expect(view.container.querySelector('.v-list-item--link')).not.toBeNull())
  await new Promise((resolve) => setTimeout(resolve, 0))
}

it('accepting from the notification answers the invitation and marks the row handled', async () => {
  respondToInvitation.mockResolvedValue({ id: 'invitation-accept', status: 'accepted' })
  const onMarkAsRead = vi.fn()
  const view = show(invitation({ invitationId: 'invitation-accept' }), onMarkAsRead)
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '接受' }))

  await waitFor(() => expect(onMarkAsRead).toHaveBeenCalledWith(7))
  expect(respondToInvitation).toHaveBeenCalledWith('invitation-accept', true)
  // Answered: it says so.
  await waitFor(() => expect(view.getByText('你已加入项目 "样例项目"')).toBeTruthy())
})

it('declining from the notification answers the invitation with a no', async () => {
  respondToInvitation.mockResolvedValue({ id: 'invitation-decline', status: 'declined' })
  const view = show(invitation({ invitationId: 'invitation-decline' }))
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '拒绝' }))

  await waitFor(() => expect(respondToInvitation).toHaveBeenCalledWith('invitation-decline', false))
})

it('a second click while the answer is on its way sends nothing more', async () => {
  let settle!: () => void
  respondToInvitation.mockImplementation(
    () => new Promise((resolve) => (settle = () => resolve({ id: 'invitation-once', status: 'accepted' })))
  )
  const view = show(invitation({ invitationId: 'invitation-once' }))
  await openRow(view)

  const accept = await view.findByRole('button', { name: '接受' })
  await fireEvent.click(accept)
  await fireEvent.click(accept)
  await fireEvent.click(view.getByRole('button', { name: '拒绝' }))
  settle()

  await waitFor(() => expect(view.queryByRole('button', { name: '接受' })).toBeNull())
  expect(respondToInvitation).toHaveBeenCalledTimes(1)
})

it('a failed answer leaves the invitation open to try again', async () => {
  respondToInvitation.mockRejectedValueOnce(new Error('network'))
  const onMarkAsRead = vi.fn()
  const view = show(invitation({ invitationId: 'invitation-retry' }), onMarkAsRead)
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '接受' }))

  await waitFor(() => expect(respondToInvitation).toHaveBeenCalled())
  expect(onMarkAsRead).not.toHaveBeenCalled()
  expect(view.getByRole('button', { name: '接受' })).toBeTruthy()
})

// 答完这一行会跟着重画（换成带链接的那一种），渲染件跟着重建：答过的状态记在桥上，
// 不记在实例里，所以重挂之后不会又冒出那两颗按钮。
it('a remount after answering does not bring the buttons back', async () => {
  respondToInvitation.mockResolvedValue({ id: 'invitation-remount', status: 'accepted' })
  const notification = invitation({ invitationId: 'invitation-remount' })
  const first = show(notification)
  await openRow(first)
  await fireEvent.click(await first.findByRole('button', { name: '接受' }))
  await waitFor(() => expect(first.queryByRole('button', { name: '接受' })).toBeNull())
  first.unmount()

  const again = show(notification)
  await openRow(again)
  expect(again.queryByRole('button', { name: '接受' })).toBeNull()
  expect(again.queryByRole('button', { name: '拒绝' })).toBeNull()
})

it('a withdrawn invitation offers nothing to answer', async () => {
  const view = show(invitation({ status: 'revoked' }))

  await waitFor(() => expect(view.getByText('项目 "样例项目" 的邀请已撤回')).toBeTruthy())
  expect(view.queryByRole('button', { name: '接受' })).toBeNull()
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})
