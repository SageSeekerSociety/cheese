import { createApp, h, nextTick } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useDocThreads } from '../../../composables/useDocThreads'

import DocThreadView from './DocThreadView.vue'

const api = vi.hoisted(() => ({ getDocThread: vi.fn(), writeDocThread: vi.fn() }))
vi.mock('../../../api/docThreads', () => api)
vi.mock('@/i18n', () => ({ t: (key: string) => key }))
const apps: ReturnType<typeof createApp>[] = []
const mounts: HTMLElement[] = []
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human' }))
})
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount())
  mounts.splice(0).forEach((mount) => mount.remove())
})
it.each([false, true])('recovers a committed reply and preserves only later typing: %s', async (laterTyping) => {
  const thread = { comment: { id: 'c' }, revision: 1, state: 'open', anchor: null, replies: [] }
  api.getDocThread.mockResolvedValue(thread)
  api.writeDocThread.mockRejectedValueOnce(new TypeError('lost receipt')).mockResolvedValue({ ...thread, revision: 2 })
  let model!: ReturnType<typeof useDocThreads>
  const app = createApp({
    setup() {
      model = useDocThreads(() => 'room')
      return () =>
        h(DocThreadView, { id: 'c', topic: 'room', actor: 'human', state: model.state, actions: model.actions })
    },
  })
  const mount = document.createElement('div')
  document.body.append(mount)
  mounts.push(mount)
  apps.push(app)
  app.mount(mount)
  await vi.waitFor(() => expect(mount.querySelector('textarea')).not.toBeNull())
  const input = mount.querySelector('textarea')!
  const type = (value: string) => {
    input.value = value
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }
  const button = (key: string) =>
    [...mount.querySelectorAll('button')].find((el) => el.textContent?.trim() === `work.room.docThread.${key}`)!
  type('submitted')
  await nextTick()
  button('send').click()
  await vi.waitFor(() => {
    expect(model.state.unknown).toBe('c')
    expect(model.state.busy).toBe(false)
  })
  await nextTick()
  if (laterTyping) type('later typing')
  button('recover').click()
  await vi.waitFor(() => expect(model.state.unknown).toBeNull())
  await nextTick()
  expect(input.value).toBe(laterTyping ? 'later typing' : '')
  expect(localStorage.getItem('cheese.doc-thread.draft.v1:human:room:c')).toBe(input.value)
  expect(api.writeDocThread.mock.calls[1]).toEqual(api.writeDocThread.mock.calls[0])
  if (!laterTyping) {
    expect(button('send').disabled).toBe(true)
    button('send').click()
    expect(api.writeDocThread).toHaveBeenCalledTimes(2)
  }
})
