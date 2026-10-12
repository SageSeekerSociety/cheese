import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLoadMoreSentinel } from './useLoadMoreSentinel'

class FakeObserver {
  static all: FakeObserver[] = []
  disconnected = false
  target: Element | null = null
  constructor(private readonly callback: IntersectionObserverCallback) {
    FakeObserver.all.push(this)
  }
  observe(el: Element) {
    this.target = el
  }
  disconnect() {
    this.disconnected = true
  }
  fire(isIntersecting: boolean) {
    this.callback([{ isIntersecting } as IntersectionObserverEntry], this as never)
  }
}

function host(onReach: () => void) {
  const present = ref(true)
  const view = render(
    defineComponent({
      setup() {
        const sentinel = useLoadMoreSentinel(onReach)
        return () => (present.value ? h('div', { ref: sentinel }) : h('span'))
      },
    })
  )
  return { view, present }
}

beforeEach(() => {
  FakeObserver.all = []
  vi.stubGlobal('IntersectionObserver', FakeObserver)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('useLoadMoreSentinel', () => {
  it('calls onReach each time the marker is in view, not when it leaves', async () => {
    const onReach = vi.fn()
    const { view } = host(onReach)
    await nextTick()
    const [observer] = FakeObserver.all
    expect(observer.target).toBe(view.container.firstElementChild)
    observer.fire(false)
    expect(onReach).not.toHaveBeenCalled()
    observer.fire(true)
    observer.fire(true)
    expect(onReach).toHaveBeenCalledTimes(2)
  })

  it('stops watching when the marker is removed, and on unmount', async () => {
    const { view, present } = host(vi.fn())
    await nextTick()
    present.value = false
    await nextTick()
    expect(FakeObserver.all).toHaveLength(1)
    expect(FakeObserver.all[0].disconnected).toBe(true)
    present.value = true
    await nextTick()
    expect(FakeObserver.all).toHaveLength(2)
    view.unmount()
    expect(FakeObserver.all[1].disconnected).toBe(true)
  })

  it('does nothing where IntersectionObserver does not exist', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    const onReach = vi.fn()
    host(onReach)
    await nextTick()
    expect(FakeObserver.all).toHaveLength(0)
    expect(onReach).not.toHaveBeenCalled()
  })
})
