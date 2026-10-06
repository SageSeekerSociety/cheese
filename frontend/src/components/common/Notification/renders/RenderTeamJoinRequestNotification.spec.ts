// 申请加入团队，在管理员「待办」的动态里就能批准或拒绝，不用再绕到团队成员页。
// 通知的形状照后端 /notifications 真实返回的写：申请是 entities.application，带它此刻的状态。
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
  TeamsApi: { approveJoinRequest: vi.fn(), rejectJoinRequest: vi.fn() },
}))

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(TeamsApi.approveJoinRequest)
    .mockReset()
    .mockResolvedValue(undefined as never)
  vi.mocked(TeamsApi.rejectJoinRequest)
    .mockReset()
    .mockResolvedValue(undefined as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// 每个用例用自己的申请 id：答过的申请记在组件之外，跨用例还在。
function joinRequest(status: string, id: string, read = false): Notification {
  return {
    id: 7,
    type: 'TEAM_JOIN_REQUEST',
    read,
    createdAt: Date.now(),
    entities: {
      requester: { id: '3', type: 'user', name: '周远', handle: 'zhouyuan' },
      team: { id: '5', type: 'team', name: '数据组', url: '/teams/data' },
      application: { id, type: 'team_membership_application', name: '', status },
    },
    contextMetadata: { message: '想一起做数据清洗' },
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

it('approves a waiting request right from the notification', async () => {
  const { view, onMarkAsRead } = await mount(joinRequest('PENDING', '62', true))

  await fireEvent.click(await view.findByRole('button', { name: '批准' }))

  await waitFor(() => expect(TeamsApi.approveJoinRequest).toHaveBeenCalledWith(5, 62))
  await waitFor(() => expect(onMarkAsRead).toHaveBeenCalledWith(7))
  // 答过的不再给人答第二次（这条本来就已读，标已读不会让行重画）。
  await waitFor(() => expect(view.queryByRole('button', { name: '批准' })).toBeNull())
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})

it('a second click while the answer is on its way sends nothing more', async () => {
  let settle!: () => void
  vi.mocked(TeamsApi.approveJoinRequest).mockImplementation(
    () => new Promise((resolve) => (settle = () => resolve(undefined as never)))
  )
  const { view } = await mount(joinRequest('PENDING', '63'))

  const approve = await view.findByRole('button', { name: '批准' })
  await fireEvent.click(approve)
  await fireEvent.click(approve)
  await fireEvent.click(view.getByRole('button', { name: '拒绝' }))
  settle()

  await waitFor(() => expect(view.queryByRole('button', { name: '批准' })).toBeNull())
  expect(TeamsApi.approveJoinRequest).toHaveBeenCalledTimes(1)
  expect(TeamsApi.rejectJoinRequest).not.toHaveBeenCalled()
})

it('rejects a waiting request right from the notification', async () => {
  const { view } = await mount(joinRequest('PENDING', '64'))

  await fireEvent.click(await view.findByRole('button', { name: '拒绝' }))

  await waitFor(() => expect(TeamsApi.rejectJoinRequest).toHaveBeenCalledWith(5, 64))
  expect(TeamsApi.approveJoinRequest).not.toHaveBeenCalled()
})

it('offers no answer once the request has been answered', async () => {
  const { view } = await mount(joinRequest('APPROVED', '65'))

  // 通用动作出现说明内容已经交上来了，此时不该再有批准 / 拒绝。
  await view.findByRole('button', { name: '删除' })
  expect(view.queryByRole('button', { name: '批准' })).toBeNull()
  expect(view.queryByRole('button', { name: '拒绝' })).toBeNull()
})
