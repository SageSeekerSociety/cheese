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

afterEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

it('shows local work, saves a machine preference and stops refreshing after leaving', async () => {
  vi.useFakeTimers()
  let state!: ReturnType<typeof useDeviceSessions>
  const view = render(
    defineComponent({
      setup() {
        state = useDeviceSessions(() => ['a', 'b'])
        onMounted(state.load)
        return () => null
      },
    })
  )
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
