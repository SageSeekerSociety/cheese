// 一个由多块各自取数的页面：在各块第一次拿到数据之前不露出来，拿齐了一起露出来；
// 但不会因为一块迟迟不来就一直看不见。下面只看页面「露没露出来」这一个量。
import type { App, Ref } from 'vue'

import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useCachedResource } from '../useCachedResource'
import { holdRevealGate, provideRevealGate } from '../useRevealGate'

import { clearPageCache } from '@/lib/pageCache'

const apps: App[] = []

afterEach(() => {
  while (apps.length) apps.pop()?.unmount()
  clearPageCache()
  vi.useRealTimers()
})

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

const settle = () => new Promise((r) => setTimeout(r, 0))

/** 一个页面，下面挂着若干块；每块在 setup 里占闸，调它的 release 就是「数据到了」。 */
function mountPage(sections: number, maxWaitMs?: number) {
  let revealed!: Ref<boolean>
  const releases: (() => void)[] = []
  const shown = ref(sections)
  const Section = defineComponent({
    setup() {
      releases.push(holdRevealGate())
      return () => h('section')
    },
  })
  const app = createApp(
    defineComponent({
      setup() {
        revealed = provideRevealGate(maxWaitMs).revealed
        return () =>
          h(
            'div',
            Array.from({ length: shown.value }, () => h(Section))
          )
      },
    })
  )
  apps.push(app)
  app.mount(document.createElement('div'))
  return { revealed, releases, shown }
}

describe('useRevealGate', () => {
  it('stays hidden until every section has its first data, then shows', async () => {
    const { revealed, releases } = mountPage(3)
    expect(revealed.value).toBe(false)
    releases[0]()
    releases[2]()
    expect(revealed.value).toBe(false)
    releases[1]()
    expect(revealed.value).toBe(true)
  })

  it('shows after the longest wait even if a section never answers', async () => {
    vi.useFakeTimers()
    const { revealed } = mountPage(2, 3000)
    vi.advanceTimersByTime(2999)
    expect(revealed.value).toBe(false)
    vi.advanceTimersByTime(1)
    expect(revealed.value).toBe(true)
  })

  it('a section that goes away no longer holds the page back', async () => {
    const { revealed, releases, shown } = mountPage(2)
    releases[0]()
    shown.value = 1
    await nextTick()
    // The remaining section already answered; the one that left must not count.
    expect(revealed.value).toBe(true)
  })

  it('a section answering twice does not open the gate for another', async () => {
    const { revealed, releases } = mountPage(2)
    releases[0]()
    releases[0]()
    expect(revealed.value).toBe(false)
  })

  it('a page without a gate is unaffected: holding is a no-op', () => {
    let release!: () => void
    const app = createApp(
      defineComponent({
        setup() {
          release = holdRevealGate()
          return () => h('div')
        },
      })
    )
    apps.push(app)
    app.mount(document.createElement('div'))
    expect(() => release()).not.toThrow()
  })

  it('a cached resource holds the page until its first answer, and not when it is already cached', async () => {
    const answer = deferred<string>()
    let revealed!: Ref<boolean>
    const Section = defineComponent({
      setup() {
        useCachedResource('gate:k', () => answer.promise)
        return () => h('section')
      },
    })
    const app = createApp(
      defineComponent({
        setup() {
          revealed = provideRevealGate().revealed
          return () => h(Section)
        },
      })
    )
    apps.push(app)
    app.mount(document.createElement('div'))
    expect(revealed.value).toBe(false)
    answer.resolve('ok')
    await settle()
    expect(revealed.value).toBe(true)

    // Second visit: the answer is cached, so the page shows at once.
    let revealedAgain!: Ref<boolean>
    const again = createApp(
      defineComponent({
        setup() {
          revealedAgain = provideRevealGate().revealed
          return () => h(Section)
        },
      })
    )
    apps.push(again)
    again.mount(document.createElement('div'))
    expect(revealedAgain.value).toBe(true)
  })
})
