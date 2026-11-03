// 额度快用完了那一条：句子是后端算好的那一种（键 + 参数，按读者的语言渲染），
// 点开去能处理它的那一页 —— 团队额度去团队的额度页，项目预算去项目的设置页。
import type { Notification } from '@/types/apiNotifications'

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

function warning(extra: Record<string, unknown>): Notification {
  return {
    id: 7,
    type: 'CREDIT_WARNING',
    read: false,
    createdAt: Date.now(),
    entities: {},
    contextMetadata: {
      content: '方案额度还剩 100 点，11月1日重置。',
      message: {
        key: 'creditWarningTeam',
        params: { remaining: 100, month: 11, day: 1, time: '00:00' },
      },
      layer: 'team',
      ...extra,
    },
  }
}

async function open(notification: Notification) {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/teams/:handle/credits', name: 'TeamsDetailCredits', component: Blank },
      { path: '/projects/:projectId/settings/:section?', name: 'project-settings', component: Blank },
      { path: '/inbox', name: 'inbox', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  const view = render(NotificationItem, {
    props: { notification, onMarkAsRead: vi.fn(), onDelete: vi.fn() },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await view.findByRole('link')
  await fireEvent.click(view.getByRole('link'))
  await waitFor(() => expect(router.currentRoute.value.path).not.toBe('/'))
  return { view, router }
}

it('a team warning names the layer, says the figure and opens the credit page', async () => {
  const { view, router } = await open(warning({ teamHandle: 'b-tree' }))

  expect(view.getByText('团队额度快用完了')).toBeTruthy()
  expect(view.getByText('方案额度还剩 100 点，11月1日重置。')).toBeTruthy()
  expect(router.currentRoute.value.fullPath).toBe('/teams/b-tree/credits')
})

it('a project budget opens that project settings page', async () => {
  const { router } = await open(
    warning({
      layer: 'project',
      teamHandle: undefined,
      projectId: '5f0f0f0f-1111-2222-3333-444455556666',
    })
  )
  expect(router.currentRoute.value.fullPath).toBe('/projects/5f0f0f0f-1111-2222-3333-444455556666/settings/environment')
})

it('with no address in the payload it falls back to the inbox', async () => {
  const { router } = await open(warning({ teamHandle: undefined }))
  expect(router.currentRoute.value.fullPath).toBe('/inbox')
})
