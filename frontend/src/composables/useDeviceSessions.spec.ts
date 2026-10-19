import { defineComponent, onMounted } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'

import { useDeviceSessions } from './useDeviceSessions'

const local = vi.hoisted(() => ({
  thisDevice: vi.fn(async () => 'machine'),
  preventSleep: vi.fn(async (enabled?: boolean) => enabled ?? true),
}))
const request = vi.hoisted(() =>
  vi.fn(async (id: string) => ({ sessions: [{ session_id: 'active', roles: [id === 'a' ? 'center' : 'executor'] }] }))
)
vi.mock('@/lib/desktop', () => ({ desktopBridge: () => local }))
vi.mock('@/api', () => ({ listDeviceRunningSessions: request }))

let state!: ReturnType<typeof useDeviceSessions>
const Host = defineComponent({
  setup() {
    state = useDeviceSessions(() => ['a', 'b'])
    onMounted(state.load)
    return () => null
  },
})

afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

it('shows local work, saves a machine preference and stops refreshing after leaving', async () => {
  vi.useFakeTimers()
  const view = render(Host)
  await vi.waitFor(() =>
    expect(state.sessions.value).toEqual([{ session_id: 'active', roles: ['center', 'executor'] }])
  )
  await state.setPreventSleep(false)
  expect(local.preventSleep).toHaveBeenLastCalledWith(false)
  expect(state.preventSleep.value).toBe(false)
  const reads = request.mock.calls.length
  view.unmount()
  await vi.advanceTimersByTimeAsync(10000)
  expect(request.mock.calls.length).toBe(reads)
})

it('lists local work when the sleep preference cannot be read', async () => {
  vi.useFakeTimers()
  local.preventSleep.mockRejectedValueOnce(new Error('Preference unavailable'))
  const view = render(Host)
  await vi.waitFor(() =>
    expect(state.sessions.value).toEqual([{ session_id: 'active', roles: ['center', 'executor'] }])
  )
  expect(state.error.value).not.toBe('')
  view.unmount()
})
