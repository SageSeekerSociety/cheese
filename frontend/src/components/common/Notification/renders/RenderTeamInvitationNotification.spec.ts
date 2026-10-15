// 团队邀请在「待办」的动态里就能接受或拒绝，不用再绕到 团队 →「待定」。
// 通知的形状照后端 /notifications 真实返回的写：邀请是 entities.application，带它此刻的状态。
// 行本身不取数：答那一下的请求在容器那份桥（`useNotificationAnswers`）上，宿主把它递给这一行。
import type { Notification } from '@/types/apiNotifications'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import { useNotificationAnswers } from '@/views/home/useNotificationAnswers'

import { TeamsApi } from '@/api/teams'
import i18n, { setLocale } from '@/i18n'

vi.mock('@/api/teams', () => ({
  TeamsApi: { acceptInvitation: vi.fn(), declineInvitation: vi.fn() },
}))

// 宿主那份桥：答过的邀请记在它模块作用域里（跨行重挂还在），这里直接取一份接上。
const { answering } = useNotificationAnswers()

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

function router() {
  const Blank = defineComponent({ render: () => h('div') })
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/teams/:handle/members', name: 'TeamsDetailMembers', component: Blank },
    ],
  })
}

async function mount(notification: Notification) {
  const r = router()
  await r.push('/')
  await r.isReady()
  const onMarkAsRead = vi.fn()
  const view = render(NotificationItem, {
    props: { notification, answering, onMarkAsRead, onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), r, i18n] },
  })
  return { view, onMarkAsRead }
}

/** 两行一起挂：锁住「一行还在路上，另一行照样答得动」。 */
async function mountTwo(first: Notification, second: Notification) {
  const r = router()
  await r.push('/')
  await r.isReady()
  const Host = defineComponent({
    render: () =>
      h('div', [
        h(NotificationItem, { notification: first, answering, onMarkAsRead: vi.fn(), onDelete: vi.fn() }),
        h(NotificationItem, { notification: second, answering, onMarkAsRead: vi.fn(), onDelete: vi.fn() }),
      ]),
  })
  return render(Host, { global: { plugins: [createVuetify({ components, directives }), r, i18n] } })
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

// 一行还在路上不该把另一行锁住：两行答的是两条邀请，各发各的。
it('two rows answer in parallel, each with its own id', async () => {
  vi.mocked(TeamsApi.acceptInvitation).mockImplementation(() => new Promise(() => {}))
  const view = await mountTwo(invitation('PENDING', '71'), invitation('PENDING', '72'))

  const accepts = await view.findAllByRole('button', { name: '接受' })
  await fireEvent.click(accepts[0])
  await fireEvent.click(accepts[1])

  await waitFor(() => expect(TeamsApi.acceptInvitation).toHaveBeenCalledTimes(2))
  expect(TeamsApi.acceptInvitation).toHaveBeenNthCalledWith(1, 71)
  expect(TeamsApi.acceptInvitation).toHaveBeenNthCalledWith(2, 72)
})

// 答完这一行会跟着重画（换成带链接的那一种），渲染件跟着重建：答过的状态记在桥上，
// 不记在实例里，所以重挂之后不会又冒出那两颗按钮。
it('a remount after answering does not bring the buttons back', async () => {
  const notification = invitation('PENDING', '73')
  const first = await mount(notification)
  await fireEvent.click(await first.view.findByRole('button', { name: '接受' }))
  await waitFor(() => expect(first.view.queryByRole('button', { name: '接受' })).toBeNull())
  cleanup()

  const again = await mount(notification)
  await again.view.findByRole('button', { name: '删除' })
  expect(again.view.queryByRole('button', { name: '接受' })).toBeNull()
  expect(again.view.queryByRole('button', { name: '拒绝' })).toBeNull()
})

it('a failed answer leaves the invitation open to try again', async () => {
  vi.mocked(TeamsApi.acceptInvitation).mockRejectedValueOnce(new Error('network'))
  const { view, onMarkAsRead } = await mount(invitation('PENDING', '74'))

  await fireEvent.click(await view.findByRole('button', { name: '接受' }))

  await waitFor(() => expect(TeamsApi.acceptInvitation).toHaveBeenCalled())
  expect(onMarkAsRead).not.toHaveBeenCalled()
  expect(view.getByRole('button', { name: '接受' })).toBeTruthy()
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

it('names the role the way the team members page does, never as its code', async () => {
  const asMember = invitation('PENDING', '46')
  const { view } = await mount(asMember)

  await view.findByText(/以 普通成员 身份邀请你加入团队/)
  expect(view.queryByText(/MEMBER/)).toBeNull()
  cleanup()

  const asAdmin = invitation('PENDING', '47')
  asAdmin.contextMetadata = { role: 'ADMIN', message: '' }
  const admin = await mount(asAdmin)

  await admin.view.findByText(/以 管理员 身份邀请你加入团队/)
  expect(admin.view.queryByText(/ADMIN/)).toBeNull()
})
