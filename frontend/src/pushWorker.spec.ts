import { runInNewContext } from 'node:vm'

import { describe, expect, it, vi } from 'vitest'

import worker from '../public/push-sw.js?raw'

const origin = 'https://cheese.example'

function startWorker(windows: object[] = []) {
  const handlers: Record<string, (event: any) => void> = {}
  const showNotification = vi.fn().mockResolvedValue(undefined)
  const openWindow = vi.fn().mockResolvedValue(null)
  runInNewContext(worker, {
    URL,
    self: {
      location: { origin },
      registration: { showNotification },
      addEventListener: (name: string, handler: (event: any) => void) => (handlers[name] = handler),
    },
    clients: { matchAll: vi.fn().mockResolvedValue(windows), openWindow },
  })
  async function push(payload: unknown) {
    let pending: Promise<unknown> | undefined
    handlers.push({ data: { json: () => payload }, waitUntil: (p: Promise<unknown>) => (pending = p) })
    await pending
    return showNotification.mock.calls.at(-1)![1].data
  }
  async function click(data: unknown) {
    let pending: Promise<unknown> | undefined
    const close = vi.fn()
    handlers.notificationclick({
      notification: { data, close },
      waitUntil: (p: Promise<unknown>) => (pending = p),
    })
    await pending
    expect(close).toHaveBeenCalledOnce()
  }
  return { push, click, openWindow, showNotification }
}

const cases = [
  [{ projectId: 'p', topicId: 'c', blockId: 'm' }, '/projects/p/topics/c?block=m'],
  [{ projectId: 'p', topicId: 'c', taskId: 't', blockId: 'm' }, '/projects/p/topics/c/tasks/t?block=m'],
  [{ projectId: 'p', topicId: 'c', threadId: 's', blockId: 'm' }, '/projects/p/topics/c/threads/s?block=m'],
  [{ projectId: 'p', topicId: 'c', taskId: 't' }, '/projects/p/topics/c/tasks/t'],
  [{ projectId: 'p' }, '/inbox'],
  [null, '/inbox'],
] as const

describe('browser push destinations', () => {
  it.each(cases)('opens the notification destination when the app is closed: %j', async (payload, path) => {
    const sw = startWorker()
    const data = await sw.push(payload)
    await sw.click(data)
    expect(new URL(sw.openWindow.mock.calls[0][0], origin).href).toBe(`${origin}${path}`)
  })

  it('reuses an open app and focuses it at the message', async () => {
    const navigate = vi.fn().mockResolvedValue({})
    const focus = vi.fn().mockResolvedValue({})
    const sw = startWorker([{ url: `${origin}/inbox`, navigate, focus }])
    await sw.click(await sw.push({ projectId: 'p', topicId: 'c', taskId: 't', blockId: 'm' }))
    expect(new URL(navigate.mock.calls[0][0], origin).pathname).toBe('/projects/p/topics/c/tasks/t')
    expect(new URL(navigate.mock.calls[0][0], origin).searchParams.get('block')).toBe('m')
    expect(focus).toHaveBeenCalledOnce()
    expect(sw.openWindow).not.toHaveBeenCalled()
  })

  it.each(['missing', 'rejected', 'closed'] as const)(
    'opens the destination if the existing app cannot navigate (%s)',
    async (state) => {
      const navigate =
        state === 'missing'
          ? undefined
          : state === 'rejected'
            ? vi.fn().mockRejectedValue(new Error('unloaded'))
            : vi.fn().mockResolvedValue(null)
      const sw = startWorker([{ url: `${origin}/inbox`, navigate, focus: vi.fn().mockResolvedValue({}) }])
      await sw.click({ url: '/projects/p/topics/c/tasks/t?block=m' })
      expect(sw.openWindow).toHaveBeenCalledOnce()
      expect(new URL(sw.openWindow.mock.calls[0][0], origin).searchParams.get('block')).toBe('m')
    }
  )

  it('tries another app window if the first one has already closed', async () => {
    const navigate = vi.fn().mockResolvedValue({})
    const focus = vi.fn().mockResolvedValue({})
    const sw = startWorker([
      { url: `${origin}/inbox`, navigate: vi.fn().mockResolvedValue(null), focus: vi.fn() },
      { url: `${origin}/inbox`, navigate, focus },
    ])
    await sw.click({ url: '/projects/p/topics/c' })
    expect(navigate).toHaveBeenCalledOnce()
    expect(focus).toHaveBeenCalledOnce()
    expect(sw.openWindow).not.toHaveBeenCalled()
  })

  it('does not reuse another site', async () => {
    const navigate = vi.fn()
    const sw = startWorker([{ url: 'https://other.example/', navigate, focus: vi.fn() }])
    await sw.click({ url: '/inbox' })
    expect(navigate).not.toHaveBeenCalled()
    expect(sw.openWindow).toHaveBeenCalledOnce()
  })

  it('keeps message-level destinations in one conversation grouped as one notification', async () => {
    const sw = startWorker()
    await sw.push({ projectId: 'p', topicId: 'c', taskId: 't', blockId: 'first' })
    await sw.push({ projectId: 'p', topicId: 'c', taskId: 't', blockId: 'second' })
    expect(sw.showNotification.mock.calls[0][1].tag).toBe(sw.showNotification.mock.calls[1][1].tag)
  })

  it.each(['https://other.example/', '//other.example/', 'javascript:alert(1)'])(
    'falls back to the inbox for an unsafe click destination: %s',
    async (url) => {
      const sw = startWorker()
      await sw.click({ url })
      expect(new URL(sw.openWindow.mock.calls[0][0], origin).href).toBe(`${origin}/inbox`)
    }
  )
})
