// 动态里的「删除」是一条不可撤销的动作，而且这颗按钮就贴在正文旁边、每一条都常驻：
// 点一下直接删会误触。这里锁住「先问一句」——确认之前不删，确认之后才删。
import type { Notification } from '@/network/api/notifications/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import NotificationItem from './NotificationItem.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

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

async function mount() {
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

  const onDelete = vi.fn()
  const view = render(NotificationItem, {
    props: { notification: notice, onMarkAsRead: vi.fn(), onDelete },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  // 通用动作要等渲染器把内容交上来才出现。
  await waitFor(() => expect(view.getByRole('button', { name: '删除' })).toBeTruthy())
  return { view, onDelete }
}

it('asks first, then deletes only on confirm', async () => {
  const { view, onDelete } = await mount()

  await fireEvent.click(view.getByRole('button', { name: '删除' }))

  // 确认框弹出，但这一刻还没有删。
  const dialog = await view.findByRole('alertdialog')
  expect(onDelete).not.toHaveBeenCalled()
  expect(within(dialog).getByText('删除这条动态？')).toBeTruthy()

  await fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

  expect(onDelete).toHaveBeenCalledTimes(1)
  expect(onDelete).toHaveBeenCalledWith(7)
})

it('leaves the notification alone when the confirm is dismissed', async () => {
  const { view, onDelete } = await mount()

  await fireEvent.click(view.getByRole('button', { name: '删除' }))
  const dialog = await view.findByRole('alertdialog')
  await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))

  expect(onDelete).not.toHaveBeenCalled()
})
