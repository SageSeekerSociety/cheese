// 题目列表顶上那一栏：只列置顶的当前公告（哪些已到期由服务端分好，这一栏只认 props），
// 一条都没有时整块不出现；点一行去公告页。
import type { SpaceAnnouncement } from '@/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, expect, it } from 'vitest'

import PinnedAnnouncements from './PinnedAnnouncements.vue'

import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))
afterEach(() => cleanup())

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

async function mount(current: SpaceAnnouncement[]) {
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
    // 读公告、算去哪都在容器里：这里直接喂它算好的「当前」公告和公告页地址。
    props: { current, target: { name: 'SpacesAnnouncements', params: { spaceId: 11 } } },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  return { view, router }
}

it('lists the pinned current announcements and leaves the rest to the announcements page', async () => {
  const { view, router } = await mount([
    announcement(1, '期中报告改为统一提交 PDF', true),
    announcement(2, '第 2 章的题目已经全部发布', false),
  ])

  await waitFor(() => expect(view.queryByText('期中报告改为统一提交 PDF')).not.toBeNull())
  expect(view.queryByText('第 2 章的题目已经全部发布')).toBeNull()

  await fireEvent.click(view.getByText('期中报告改为统一提交 PDF'))
  await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/spaces/11/announcements'))
})

it('shows nothing when no current announcement is pinned', async () => {
  const { view } = await mount([announcement(2, '第 2 章的题目已经全部发布', false)])
  expect(view.container.querySelector('a')).toBeNull()
})
