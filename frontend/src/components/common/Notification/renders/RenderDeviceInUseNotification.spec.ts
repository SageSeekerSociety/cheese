import type { Notification } from '@/network/api/notifications/types'

import { render } from '@testing-library/vue'
import { beforeAll, expect, it } from 'vitest'

import RenderDeviceInUseNotification from './RenderDeviceInUseNotification.vue'

import { getNotificationRenderer } from '@/components/common/Notification/registry'
import i18n, { setLocale } from '@/i18n'

beforeAll(() => setLocale('zh-CN'))

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
    global: { plugins: [i18n] },
  })

  expect(view.container.textContent).toMatch(/@Cedar\s*开始在「workstation」上工作/)
  expect(view.container.querySelector('.mention')?.textContent).toBe('@Cedar')
  expect(view.getByText('Orchard · Pricing · 能访问整台电脑')).toBeTruthy()
})

it('says nothing about the whole machine when the agent cannot see it', () => {
  const view = render(RenderDeviceInUseNotification, {
    props: { notification: notice(false) },
    global: { plugins: [i18n] },
  })

  expect(view.getByText('Orchard · Pricing')).toBeTruthy()
  expect(view.queryByText(/能访问整台电脑/)).toBeNull()
})
