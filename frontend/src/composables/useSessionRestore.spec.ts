// The cold-open restore layer: what a returning reader sees while their stored
// session is checked with the server, and when it cannot be.
import { createApp, defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const account = vi.hoisted(() => ({ state: null as null | { restorePhase: string } }))

vi.mock('@/services/account', async () => {
  const { reactive } = await import('vue')
  account.state = reactive({ restorePhase: 'idle' })
  return {
    default: {
      get restorePhase() {
        return account.state!.restorePhase
      },
      retryRestore: vi.fn(async () => false),
      dismissRestore: vi.fn(),
    },
  }
})
vi.mock('@/router/home', () => ({ landingForMember: vi.fn(async () => ({ name: 'inbox' })) }))

import { useSessionRestore } from './useSessionRestore'

function shownLayer() {
  let visible!: () => boolean
  createApp(
    defineComponent({
      setup() {
        const restore = useSessionRestore()
        visible = () => restore.visible.value
        return () => h('div')
      },
    })
  ).mount(document.createElement('div'))
  return visible
}

describe('the restore layer on a cold open', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    account.state!.restorePhase = 'idle'
  })
  afterEach(() => vi.useRealTimers())

  it('stays out of sight while the stored session is being confirmed, however long that takes', async () => {
    account.state!.restorePhase = 'restoring'
    const visible = shownLayer()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(visible()).toBe(false)
  })

  it('appears when the session cannot be confirmed', async () => {
    const visible = shownLayer()
    account.state!.restorePhase = 'restoring'
    await vi.advanceTimersByTimeAsync(2_000)
    account.state!.restorePhase = 'unreachable'
    await vi.advanceTimersByTimeAsync(0)
    expect(visible()).toBe(true)
  })
})
