import type { Notification } from '@/types/apiNotifications'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, expect, it } from 'vitest'

import RenderDeviceInUseNotification from './RenderDeviceInUseNotification.vue'

import { getNotificationRenderer } from '@/components/common/Notification/registry'
import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))
// 这条通知里的 agent 走 `UserRef` → `IdentityView` → `UserAvatar`（Vuetify 的 `v-avatar`），
// 所以要挂真的 Vuetify：起一个只带组件的实例就够，`UserAvatar` 不再要 defaults。
const vuetify = createVuetify({ components, directives })

function notice(machineAccess: boolean): Notification {
  return {
    id: 1,
    type: 'DEVICE_IN_USE',
    read: false,
    createdAt: 0,
    entities: {},
    contextMetadata: {
      projectName: 'Orchard',
      topicTitle: 'Pricing',
      agentName: 'Cedar',
      deviceName: 'workstation',
      machineAccess,
      teamHandle: 'crew',
    },
  }
}

it('tells the owner which agent works on their device, where, and what it can see', () => {
  expect(getNotificationRenderer('DEVICE_IN_USE')).toBe(RenderDeviceInUseNotification)
  const view = render(RenderDeviceInUseNotification, {
    props: { notification: notice(true) },
    global: { plugins: [i18n, vuetify] },
  })

  expect(view.container.textContent).toContain('开始在「workstation」上工作')
  // agent 是一颗 @chip（头像 + @名字），不是把 `@Cedar` 直接贴在句子里。看 chip 里那个
  // 可见的名字节点：整段文本会被头像那圈首字母挡住。
  expect(view.container.querySelector('.mention .identity-view__name')?.textContent).toBe('@Cedar')
  expect(view.getByText('Orchard · Pricing · 能访问整台电脑')).toBeTruthy()
})

it('says nothing about the whole machine when the agent cannot see it', () => {
  const view = render(RenderDeviceInUseNotification, {
    props: { notification: notice(false) },
    global: { plugins: [i18n, vuetify] },
  })

  expect(view.getByText('Orchard · Pricing')).toBeTruthy()
  expect(view.queryByText(/能访问整台电脑/)).toBeNull()
})
