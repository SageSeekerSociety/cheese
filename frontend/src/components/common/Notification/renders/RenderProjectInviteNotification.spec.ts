import type { Notification } from '@/network/api/notifications/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import i18n, { setLocale } from '@/i18n'

const respondToInvitation = vi.fn()
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  respondToInvitation: (...args: unknown[]) => respondToInvitation(...args),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeAll(() => setLocale('zh-CN'))
beforeEach(() => respondToInvitation.mockReset())

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
    props: { notification, onMarkAsRead, onDelete: vi.fn() },
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
  respondToInvitation.mockResolvedValue({ id: 'invitation-1', status: 'accepted' })
  const onMarkAsRead = vi.fn()
  const view = show(invitation(), onMarkAsRead)
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '接受' }))

  await waitFor(() => expect(onMarkAsRead).toHaveBeenCalledWith(7))
  expect(respondToInvitation).toHaveBeenCalledWith('invitation-1', true)
  // Answered: it says so.
  await waitFor(() => expect(view.getByText('你已加入项目 "样例项目"')).toBeTruthy())
})

it('declining from the notification answers the invitation with a no', async () => {
  respondToInvitation.mockResolvedValue({ id: 'invitation-1', status: 'declined' })
  const view = show(invitation())
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '拒绝' }))

  await waitFor(() => expect(respondToInvitation).toHaveBeenCalledWith('invitation-1', false))
})

it('a failed answer leaves the invitation open to try again', async () => {
  respondToInvitation.mockRejectedValueOnce(new Error('network'))
  const onMarkAsRead = vi.fn()
  const view = show(invitation(), onMarkAsRead)
  await openRow(view)

  await fireEvent.click(await view.findByRole('button', { name: '接受' }))

  await waitFor(() => expect(respondToInvitation).toHaveBeenCalled())
  expect(onMarkAsRead).not.toHaveBeenCalled()
  expect(view.getByRole('button', { name: '接受' })).toBeTruthy()
})

it('a withdrawn invitation offers nothing to answer', async () => {
  const view = show(invitation({ status: 'revoked' }))

  await waitFor(() => expect(view.getByText('项目 "样例项目" 的邀请已撤回')).toBeTruthy())
  expect(view.queryByRole('button', { name: '接受' })).toBeNull()
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})
