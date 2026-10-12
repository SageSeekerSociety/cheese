import type { Notification } from '@/types/apiNotifications'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'
import { getNotificationMark } from '../registry'

import RenderRoomNoticeNotification from './RenderRoomNoticeNotification.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

// 平台在房间里说的、要人动手的那一句：标题是房间那一行（键 + 参数，按读者的语言
// 渲染），副标题说在哪个房间。卡一有决定，服务端把结局并进 `outcome`。
function notice(outcome?: string): Notification {
  return {
    id: 1,
    type: 'ROOM_NOTICE',
    read: true,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectId: 'p1',
      topicId: 't1',
      topicTitle: '云端',
      message: { key: 'acceptReady', params: { pr: 7, reviewer: 'lz123y' } },
      ...(outcome ? { outcome } : {}),
    },
  }
}

function open(notification: Notification) {
  return render(RenderRoomNoticeNotification, {
    props: { notification },
    global: { plugins: [i18n] },
  })
}

it('an open notice says the room line and where it is', () => {
  const view = open(notice())

  expect(view.getByText('PR #7 可以合并了，等 lz123y 采纳')).toBeTruthy()
  expect(view.getByText('在「云端」')).toBeTruthy()
})

it('a settled notice says the outcome instead of still waiting', () => {
  const view = open(notice('accepted'))

  expect(view.getByText('在「云端」，已采纳')).toBeTruthy()
})

it('an outcome this build does not know still reads as settled', () => {
  const view = open(notice('superseded'))

  expect(view.getByText('在「云端」，已办结')).toBeTruthy()
})

// 画成警示色、铃铛，等于说它还在等人：办完的那一条换成对勾，颜色退成 success。
it('a settled notice stops being drawn as waiting on someone', () => {
  expect(getNotificationMark(notice('rejected'))).toEqual({
    icon: 'mdi-check-circle-outline',
    color: 'success',
  })
  expect(getNotificationMark(notice()).color).toBe('warning')
})

it.each([
  [{}, 'workspace-topic'],
  [{ taskId: 'task-9' }, 'workspace-task'],
  [{ threadId: 'thread-7' }, 'workspace-thread'],
])('clicking a platform notice opens its conversation at the message (%j)', async (where, name) => {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/p/:projectId/t/:topicId', name: 'workspace-topic', component: Blank },
      { path: '/p/:projectId/t/:topicId/tasks/:taskId', name: 'workspace-task', component: Blank },
      { path: '/p/:projectId/t/:topicId/threads/:threadId', name: 'workspace-thread', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  const notification = notice()
  notification.read = false
  Object.assign(notification.contextMetadata, where, { blockId: 'block-42' })
  const onMarkAsRead = vi.fn()
  const view = render(NotificationItem, {
    props: { notification, onMarkAsRead, onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(view.container.querySelector('.v-list-item--link')).not.toBeNull())
  await fireEvent.click(view.container.querySelector('.v-list-item')!)
  await waitFor(() => expect(router.currentRoute.value.name).toBe(name))
  expect(router.currentRoute.value.params).toMatchObject({ projectId: 'p1', topicId: 't1', ...where })
  expect(router.currentRoute.value.query.block).toBe('block-42')
  expect(onMarkAsRead).toHaveBeenCalledWith(1)
})
