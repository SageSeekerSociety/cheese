import { createApp, defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'
import { announceComments } from '../lib/docCommentSignals'

import { useDocThreads } from './useDocThreads'

const api = vi.hoisted(() => ({ listDocThreads: vi.fn(), writeDocThread: vi.fn() }))
vi.mock('../api/docThreads', () => api)
const apps: ReturnType<typeof createApp>[] = []
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  vi.resetAllMocks()
  api.listDocThreads.mockResolvedValue({ data: [thread()], total: 1 })
})
afterEach(() => apps.splice(0).forEach((app) => app.unmount()))
function thread(over: Record<string, unknown> = {}) {
  return { comment: { id: 'c' }, revision: 1, state: 'open', replies: [], answering: null, ...over }
}
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
      const { state, actions, refresh } = setup()
      await refresh()
      const committed = new Set<string>()
      let attempt = 0
      api.writeDocThread.mockImplementation(async (_room, _id, _action, body) => {
        expect(JSON.parse(localStorage.getItem('cheese.doc-thread.operation.v1:human:room')!).body).toEqual(body)
        attempt++
        if (attempt === 2) throw new ApiError(403, 'membership revoked before receipt lookup')
        committed.add(body.operation_id)
        if (attempt === 1) throw new TypeError('response lost after server commit')
        return thread({ revision: 2 })
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
      expect(state.threads.find((t) => t.comment.id === 'c')?.revision).toBe(2)
    }
  )
})

describe('threads follow the room', () => {
  it('reads the threads again when the room says they changed, and shows how far the agent has got', async () => {
    const { state, refresh } = setup()
    await refresh()
    announceComments('room', { kind: 'activity', thread: 'c', state: 'working', tool: 'cheese_doc_get' })
    expect(state.activity.c).toEqual({ state: 'working', tool: 'cheese_doc_get' })

    api.listDocThreads.mockResolvedValue({
      data: [thread({ revision: 2, replies: [{ sequence: 1, comment: { id: 'r', content: '答' } }] })],
      total: 1,
    })
    announceComments('room', { kind: 'changed' })
    await vi.waitFor(() => expect(state.threads[0].replies).toHaveLength(1))
    // The answer is in: the agent is no longer at work on it.
    expect(state.activity.c).toBeUndefined()
  })

  it('does not hear another room', async () => {
    const { state, refresh } = setup()
    await refresh()
    announceComments('elsewhere', { kind: 'activity', thread: 'c', state: 'queued' })
    expect(state.activity.c).toBeUndefined()
  })
})
