// 「通知」：读回来的默认就是设计稿那一份；改一格就存一格，存好了在页头说「已保存」，
// 存失败就把这一格放回原值、说「保存失败」。关掉浏览器推送会真的退订这台电脑。
import type { NotificationPreferences } from '@/lib/notificationPreferences'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Notifications from './Notifications.vue'

import { setLocale } from '@/i18n'

const { getMock, saveMock, disableMock, enableMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  saveMock: vi.fn(),
  disableMock: vi.fn(),
  enableMock: vi.fn(),
}))

vi.mock('@/api/notificationPreferences', () => ({
  getNotificationPreferences: getMock,
  saveNotificationPreferences: saveMock,
}))

vi.mock('@/services/webPush', () => ({
  enablePush: enableMock,
  disablePush: disableMock,
}))

/** 设计稿那一份默认（也就是后端没存过时回的）：邮件停「摘要」，只有站内全开，
 *  「有人回应了我」只进站内。 */
function defaults(): NotificationPreferences {
  return {
    inAppEnabled: true,
    pushEnabled: true,
    emailMode: 'digest',
    quietHoursEnabled: true,
    quietHoursStart: '22:00',
    quietHoursEnd: '08:00',
    digestCadence: 'weekly',
    events: {
      mention: { inApp: true, push: true, email: true },
      reply: { inApp: true, push: false, email: true },
      reaction: { inApp: true, push: false, email: false },
      waitsOnMe: { inApp: true, push: true, email: true },
      deviceInUse: { inApp: true, push: true, email: false },
      invitation: { inApp: true, push: true, email: true },
      announcement: { inApp: true, push: false, email: true },
      billing: { inApp: true, push: false, email: true },
    },
  }
}

function show() {
  return render(Notifications, { global: { plugins: [createVuetify({ components, directives })] } })
}

/** 一样是关掉推送那颗开关（页头「浏览器推送」那一行的 v-switch）。 */
async function pushSwitch(view: ReturnType<typeof show>) {
  return (await view.findByLabelText('浏览器推送')) as HTMLInputElement
}

beforeEach(() => {
  setLocale('zh-CN')
  getMock.mockReset()
  saveMock.mockReset()
  disableMock.mockReset()
  enableMock.mockReset()
  getMock.mockResolvedValue(defaults())
  saveMock.mockImplementation((prefs: NotificationPreferences) => Promise.resolve(prefs))
  disableMock.mockResolvedValue(undefined)
  enableMock.mockResolvedValue(true)
})

afterEach(() => cleanup())

describe('notification settings', () => {
  it('shows the design defaults', async () => {
    const view = show()
    // 邮件停在「摘要」（设计稿那一格）。
    const digest = await view.findByRole('radio', { name: '摘要' })
    expect(digest.getAttribute('aria-checked')).toBe('true')
    // 「有人回应了我」默认只进站内：它的推送那一格是关的。
    const reactionPush = await view.findByRole('button', { name: '切换「有人回应了我的消息」的推送' })
    expect(reactionPush.getAttribute('aria-pressed')).toBe('false')
    const reactionInApp = await view.findByRole('button', { name: '切换「有人回应了我的消息」的站内' })
    expect(reactionInApp.getAttribute('aria-pressed')).toBe('true')
  })

  it('saves a channel change and says so', async () => {
    const view = show()
    const instant = await view.findByRole('radio', { name: '即时' })
    await fireEvent.click(instant)
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1))
    expect(saveMock.mock.calls[0][0].emailMode).toBe('instant')
    await waitFor(() => expect(view.getByText('已保存')).toBeTruthy())
  })

  it('saves a matrix cell', async () => {
    const view = show()
    const cell = await view.findByRole('button', { name: '切换「有人回应了我的消息」的站内' })
    await fireEvent.click(cell)
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1))
    expect(saveMock.mock.calls[0][0].events.reaction.inApp).toBe(false)
    await waitFor(() => expect(cell.getAttribute('aria-pressed')).toBe('false'))
  })

  it('unsubscribes this browser when push is turned off', async () => {
    const view = show()
    const control = await pushSwitch(view)
    await fireEvent.input(control, { target: { checked: false } })
    await waitFor(() => expect(disableMock).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1))
    expect(saveMock.mock.calls[0][0].pushEnabled).toBe(false)
    expect(enableMock).not.toHaveBeenCalled()
  })

  it('puts a change back when the server refuses it', async () => {
    saveMock.mockRejectedValue(new Error('nope'))
    const view = show()
    const cell = await view.findByRole('button', { name: '切换「有人回应了我的消息」的站内' })
    await fireEvent.click(cell)
    await waitFor(() => expect(view.getByText('保存失败')).toBeTruthy())
    // 放回原值：这一格还是开着的。
    expect(cell.getAttribute('aria-pressed')).toBe('true')
  })
})
