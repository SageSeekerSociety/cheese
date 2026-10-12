// @vitest-environment jsdom
// The notifications domain's request contract: one case per method, each
// spelling out the method, path, query and body the wire carries, plus the
// payload the caller gets back. Notifications ride the new origin.
import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import {
  type ContractCase,
  contractEnvironment,
  contractRequest,
  envelope,
  newer,
  server,
} from './requestContract.spec.helper'

contractEnvironment()

let notifications: typeof import('./notifications').NotificationsApi

beforeAll(async () => {
  ;({ NotificationsApi: notifications } = await import('./notifications'))
})

const cases: { name: string; test: ContractCase; call: () => Promise<unknown> }[] = [
  {
    name: 'list',
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
    call: () => notifications.list({ type: 'MENTION', read: false, pageStart: 'cursor-1', pageSize: 20 }),
  },
  {
    name: 'del',
    test: { method: 'DELETE', path: '/notifications/9', args: [9] },
    call: () => notifications.del(9),
  },
  {
    name: 'updateStatus',
    test: { method: 'PATCH', path: '/notifications/9', args: [9, true], body: { read: true } },
    call: () => notifications.updateStatus(9, true),
  },
  {
    name: 'markAllAsRead',
    test: { method: 'PUT', path: '/notifications/status', args: [], body: { read: true } },
    call: () => notifications.markAllAsRead(),
  },
  {
    name: 'getUnreadNotificationCount',
    test: { method: 'GET', path: '/notifications/unread-count', args: [] },
    call: () => notifications.getUnreadNotificationCount(),
  },
  {
    name: 'liveToken',
    test: { method: 'POST', path: '/notifications/live-token', args: [] },
    call: () => notifications.liveToken(),
  },
]

describe('notifications: every method keeps its request and payload', () => {
  it.each(cases)('$name sends the recorded request and returns the payload', async (entry) => {
    await contractRequest(entry.test, entry.call)
  })
})

describe('notifications: a command keeps its success bit', () => {
  it.each(['null', '204'])('delete resolves to true on a %s success, not a caught failure', async (kind) => {
    server.use(
      http.delete(`${newer}/notifications/9`, () =>
        kind === '204' ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(envelope(null))
      )
    )
    await expect(notifications.del(9)).resolves.toBe(true)
  })
})
