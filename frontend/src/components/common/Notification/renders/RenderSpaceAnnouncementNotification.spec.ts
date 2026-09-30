// 动态里的一条公告：点开去的是发它的那个空间的公告页，并且这一条随之标为已读。
import type { Notification } from '@/network/api/notifications/types'

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

const notice: Notification = {
  id: 7,
  type: 'SPACE_ANNOUNCEMENT',
  read: false,
  createdAt: Date.now(),
  entities: {},
  contextMetadata: {
    spaceId: 11,
    spaceName: '数据分析课',
    announcementId: 3,
    title: '期中报告改为统一提交 PDF',
    excerpt: '截止时间不变',
    authorName: '林夏',
  },
}

it('clicking it opens that space’s announcements and marks it read', async () => {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/spaces/:spaceId/announcements', name: 'SpacesAnnouncements', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  const onMarkAsRead = vi.fn()

  const view = render(NotificationItem, {
    props: { notification: notice, onMarkAsRead, onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })

  expect(view.getByText('期中报告改为统一提交 PDF')).toBeTruthy()
  // 带链接的那一版要等渲染器把内容交上来才出现。
  await waitFor(() => expect(view.container.querySelector('.v-list-item--link')).not.toBeNull())
  await fireEvent.click(view.container.querySelector('.v-list-item')!)

  await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/spaces/11/announcements'))
  expect(onMarkAsRead).toHaveBeenCalledWith(7)
})
