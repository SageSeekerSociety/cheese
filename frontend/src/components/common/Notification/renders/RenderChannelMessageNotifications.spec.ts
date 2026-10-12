import type { Notification, NotificationType } from '@/types/apiNotifications'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

// 频道里的一条消息引出的两种通知：@ 了我，和我参与的支线里有人回复了。
function said(type: NotificationType, extra: Record<string, string> = {}): Notification {
  return {
    id: 7,
    type,
    read: false,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectId: 'p1',
      topicId: 't1',
      topicTitle: '前端',
      blockId: 'block-3',
      author: 'alice',
      authorName: 'Alice',
      content: '看一下这个方案',
      ...extra,
    },
  }
}

async function openInTheInbox(notification: Notification) {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/p/:projectId/t/:topicId', name: 'workspace-topic', component: Blank },
      { path: '/p/:projectId/tasks/:taskId', name: 'workspace-task', component: Blank },
      { path: '/p/:projectId/t/:topicId/threads/:threadId', name: 'workspace-thread', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  const view = render(NotificationItem, {
    props: { notification, onMarkAsRead: vi.fn(), onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(view.container.querySelector('.v-list-item--link')).not.toBeNull())
  return { view, router }
}

it('a mention in a channel says who and where, and opens on the message', async () => {
  const { view, router } = await openInTheInbox(said('MENTION'))

  expect(view.getByText('Alice 在「前端」提到了你')).toBeTruthy()
  expect(view.getByText('看一下这个方案')).toBeTruthy()
  await fireEvent.click(view.container.querySelector('.v-list-item')!)
  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-topic'))
  expect(router.currentRoute.value.params).toMatchObject({ projectId: 'p1', topicId: 't1' })
  expect(router.currentRoute.value.query.block).toBe('block-3')
})

it('a reply in a 支线 says who and where, and opens the 支线 on the reply', async () => {
  const { view, router } = await openInTheInbox(said('THREAD_REPLY', { threadId: 'thread-5' }))

  expect(view.getByText('Alice 在「前端」的支线里回复了')).toBeTruthy()
  expect(view.getByText('看一下这个方案')).toBeTruthy()
  await fireEvent.click(view.container.querySelector('.v-list-item')!)
  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-thread'))
  expect(router.currentRoute.value.params).toMatchObject({ topicId: 't1', threadId: 'thread-5' })
  expect(router.currentRoute.value.query.block).toBe('block-3')
})
