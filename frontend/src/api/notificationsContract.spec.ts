// @vitest-environment jsdom
// The notifications domain's payload contract. The pilot in
// `migrationContract.spec.ts` only ever exercised the unread count; every other
// method here (`list`, `del`, `updateStatus`, `markAllAsRead`, `liveToken`) now
// gets a real old-versus-new comparison of the observable request and the
// answer (the old envelope's `.data` versus the new payload). Notifications
// ride the new origin, so both sides answer from `${newer}`.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import {
  compareRequests,
  type ContractCase,
  contractEnvironment,
  envelope,
  newer,
  server,
} from './migrationContract.spec.helper'

contractEnvironment()

let oldNotifications: typeof import('@/network/api/notifications').NotificationsApi
let newNotifications: typeof import('./notifications').NotificationsApi

beforeAll(async () => {
  ;({ NotificationsApi: oldNotifications } = await import('@/network/api/notifications'))
  ;({ NotificationsApi: newNotifications } = await import('./notifications'))
})

// Each new method paired with the old name it replaced, and the arguments that
// reach the wire. `getUnreadNotificationCount` was the pilot name; the others
// are 1:1.
const cases: Record<string, { test: ContractCase; old: keyof typeof oldNotifications }> = {
  list: {
    test: {
      method: 'GET',
      path: '/notifications',
      args: [{ type: 'MENTION', read: false, pageStart: 'cursor-1', pageSize: 20 }],
      query: [
        ['type', 'MENTION'],
        ['read', 'false'],
        ['pageStart', 'cursor-1'],
        ['pageSize', '20'],
      ],
    },
    old: 'list',
  },
  del: { test: { method: 'DELETE', path: '/notifications/9', args: [9] }, old: 'del' },
  updateStatus: {
    test: { method: 'PATCH', path: '/notifications/9', args: [9, true], body: { read: true } },
    old: 'updateStatus',
  },
  markAllAsRead: {
    test: { method: 'PUT', path: '/notifications/status', args: [], body: { read: true } },
    old: 'markAllAsRead',
  },
  getUnreadNotificationCount: {
    test: { method: 'GET', path: '/notifications/unread-count', args: [] },
    old: 'getUnreadCount',
  },
  liveToken: { test: { method: 'POST', path: '/notifications/live-token', args: [] }, old: 'liveToken' },
}

describe('notifications migration: every method keeps its request and payload', () => {
  it.each(Object.entries(cases))('%s preserves the request and returns the payload', async (name, entry) => {
    await compareRequests(
      entry.test,
      () => (oldNotifications[entry.old] as (...args: unknown[]) => Promise<unknown>)(...entry.test.args),
      () =>
        (newNotifications[name as keyof typeof newNotifications] as (...args: unknown[]) => Promise<unknown>)(
          ...entry.test.args
        )
    )
  })
})

describe('notifications migration: a command keeps its success bit', () => {
  it.each(['null', '204'])('delete resolves to true on a %s success, not a caught failure', async (kind) => {
    server.use(
      http.delete(`${newer}/notifications/9`, () =>
        kind === '204' ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(envelope(null))
      )
    )
    await expect(oldNotifications.del(9)).resolves.not.toBeUndefined()
    await expect(newNotifications.del(9)).resolves.toBe(true)
  })
})
