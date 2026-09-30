// 题目列表顶上那一栏：只列置顶的当前公告（哪些已到期由服务端分好），一条都没有时
// 整块不出现；点一行去公告页。
import type { SpaceAnnouncement } from '@/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'

const listAnnouncements = vi.fn()
vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { listAnnouncements: (...a: unknown[]) => listAnnouncements(...a) },
}))

import PinnedAnnouncements from './PinnedAnnouncements.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function announcement(id: number, title: string, pinned: boolean): SpaceAnnouncement {
  return {
    id,
    spaceId: 11,
    title,
    content: '',
    pinned,
    expiresAt: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    author: null,
  }
}

async function mount(current: SpaceAnnouncement[], expired: SpaceAnnouncement[] = []) {
  listAnnouncements.mockImplementation(async () => ({ data: { current, expired, notifyCount: null } }))
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks', component: Blank },
      { path: '/spaces/:spaceId/announcements', name: 'SpacesAnnouncements', component: Blank },
    ],
  })
  await router.push('/spaces/11/tasks')
  await router.isReady()
  const view = render(PinnedAnnouncements, {
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(listAnnouncements).toHaveBeenCalledWith(11))
  return { view, router }
}

it('lists the pinned current announcements and leaves the rest to the announcements page', async () => {
  const { view, router } = await mount(
    [announcement(1, '期中报告改为统一提交 PDF', true), announcement(2, '第 2 章的题目已经全部发布', false)],
    [announcement(3, '国庆假期助教不在线', true)]
  )

  await waitFor(() => expect(view.queryByText('期中报告改为统一提交 PDF')).not.toBeNull())
  expect(view.queryByText('第 2 章的题目已经全部发布')).toBeNull()
  expect(view.queryByText('国庆假期助教不在线')).toBeNull()

  await fireEvent.click(view.getByText('期中报告改为统一提交 PDF'))
  await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/spaces/11/announcements'))
})

it('shows nothing when no current announcement is pinned', async () => {
  const { view } = await mount([announcement(2, '第 2 章的题目已经全部发布', false)])
  // 等那次读取落地：落地之前本来就什么都没有。
  await new Promise((resolve) => setTimeout(resolve, 0))

  expect(view.container.querySelector('a')).toBeNull()
})
