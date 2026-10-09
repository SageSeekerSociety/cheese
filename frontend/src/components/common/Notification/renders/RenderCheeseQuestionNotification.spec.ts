import type { Notification } from '@/network/api/notifications/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, expect, it, vi } from 'vitest'

import NotificationItem from '../NotificationItem.vue'

import RenderCheeseQuestionNotification from './RenderCheeseQuestionNotification.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

function question(extra: Record<string, string> = {}): Notification {
  return {
    id: 1,
    type: 'CHEESE_QUESTION',
    read: false,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectId: 'p1',
      topicId: 't1',
      topicTitle: '预算复核',
      question: '预算按哪个口径统计',
      ...extra,
    },
  }
}

it('an open question says the turn is paused and waits for an answer', () => {
  const view = render(RenderCheeseQuestionNotification, {
    props: { notification: question() },
    global: { plugins: [i18n] },
  })

  expect(view.getByText('预算按哪个口径统计')).toBeTruthy()
  expect(view.getByText('在「预算复核」，已暂停，待你回答')).toBeTruthy()
})

it('an answered question says what was chosen, not that it is still waiting', () => {
  const view = render(RenderCheeseQuestionNotification, {
    props: { notification: question({ answered: '按项目' }) },
    global: { plugins: [i18n] },
  })

  expect(view.getByText('在「预算复核」，已回答：按项目')).toBeTruthy()
  expect(view.queryByText(/待你回答/)).toBeNull()
})

// 收件箱「动态」里点一条提问：落到的不是房间最新那几条，而是问题本身 —— 地址里带上
// `?block=`，房间页拿它把人停在提问那条消息上（`TopicView` 读 route.query.block，对话
// 栏按它把窗口开到那儿）。这条钉的是「那个 id 真的走到了地址里」——一个查询串名字写
// 错，链接照样"看起来像个链接"，点开却落在房间末尾。
async function clickInTheInbox(extra: Record<string, string>) {
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
  const onMarkAsRead = vi.fn()

  const view = render(NotificationItem, {
    props: {
      notification: question(extra),
      onMarkAsRead,
      onDelete: vi.fn(),
    },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })

  // 带链接的那一版要等渲染器把内容交上来才出现。
  await waitFor(() => expect(view.container.querySelector('.v-list-item--link')).not.toBeNull())
  await fireEvent.click(view.container.querySelector('.v-list-item')!)
  return { router, onMarkAsRead }
}

it('clicking a question in the inbox opens the room at the question itself', async () => {
  const { router, onMarkAsRead } = await clickInTheInbox({ blockId: 'block-42' })

  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-topic'))
  expect(router.currentRoute.value.params).toMatchObject({ projectId: 'p1', topicId: 't1' })
  expect(router.currentRoute.value.query.block).toBe('block-42')
  expect(onMarkAsRead).toHaveBeenCalledWith(1)
})

// 芝士在任务里问的题：那条消息在任务自己的线上，频道主线上没有它。落回频道的话，房间
// 页按 `?block=` 找那条消息找不到，人只看到一句「这条消息已不存在」—— 实况就是这条。
it('a question asked in a task opens that task, not its channel', async () => {
  const { router } = await clickInTheInbox({ blockId: 'block-42', taskId: 'task-9' })

  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-task'))
  expect(router.currentRoute.value.params).toMatchObject({ projectId: 'p1', taskId: 'task-9' })
  expect(router.currentRoute.value.query.block).toBe('block-42')
})

// 支线里问的那道题同理：那条消息在那条支线的线上。
it('a question asked in a 支线 opens that 支线', async () => {
  const { router } = await clickInTheInbox({ blockId: 'block-42', threadId: 'thread-7' })

  await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-thread'))
  expect(router.currentRoute.value.params).toMatchObject({
    projectId: 'p1',
    topicId: 't1',
    threadId: 'thread-7',
  })
  expect(router.currentRoute.value.query.block).toBe('block-42')
})
