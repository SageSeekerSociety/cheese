import { createApp, defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'

import { useDocThreads } from './useDocThreads'

const api = vi.hoisted(() => ({ getDocThread: vi.fn(), writeDocThread: vi.fn() }))
vi.mock('../api/docThreads', () => api)
const apps: ReturnType<typeof createApp>[] = []
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  vi.resetAllMocks()
  api.getDocThread.mockResolvedValue({ comment: { id: 'c' }, revision: 1, state: 'open', anchor: null, replies: [] })
})
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))
function setup() {
  let threads!: ReturnType<typeof useDocThreads>
  const app = createApp(
    defineComponent({
      setup() {
        threads = useDocThreads(() => 'room')
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return threads
}
describe('comment thread operation recovery', () => {
  it.each(['replies', 'resolve', 'reopen'] as const)(
    'retains unknown %s after denied replay and never creates another write',
    async (action) => {
      const { state, actions } = setup()
      await actions.load('c')
      const committed = new Set<string>()
      let attempt = 0
      api.writeDocThread.mockImplementation(async (_room, _id, _action, body) => {
        expect(JSON.parse(localStorage.getItem('cheese.doc-thread.operation.v1:human:room')!).body).toEqual(body)
        attempt++
        if (attempt === 2) throw new ApiError(403, 'membership revoked before receipt lookup')
        committed.add(body.operation_id)
        if (attempt === 1) throw new TypeError('response lost after server commit')
        return { comment: { id: 'c' }, revision: 2, state: 'open', anchor: null, replies: [] }
      })
      const send = () => (action === 'replies' ? actions.reply('c', 'reply') : actions[action]('c'))
      await expect(send()).rejects.toThrow('response lost')
      const stored = localStorage.getItem('cheese.doc-thread.operation.v1:human:room')
      await expect(actions.recover('c')).rejects.toThrow('membership revoked')
      expect(state.unknown).toBe('c')
      expect(localStorage.getItem('cheese.doc-thread.operation.v1:human:room')).toBe(stored)
      await expect(send()).rejects.toThrow('needs recovery')
      expect(api.writeDocThread).toHaveBeenCalledTimes(2)
      await actions.recover('c')
      const calls = api.writeDocThread.mock.calls
      expect(calls[1]).toEqual(calls[0])
      expect(calls[2]).toEqual(calls[0])
      expect(committed.size).toBe(1)
      expect(state.unknown).toBeNull()
      expect(localStorage.getItem('cheese.doc-thread.operation.v1:human:room')).toBeNull()
      // An older GET after replay cannot downgrade the installed receipt.
      expect(state.threads.c.revision).toBe(2)
    }
  )
})
