import { afterEach, describe, expect, it, vi } from 'vitest'

import { firstPageReady, stylesheetsLoaded } from './bootReady'

function settledWithin(promise: Promise<unknown>, ms: number) {
  return Promise.race([promise.then(() => true), new Promise((resolve) => setTimeout(() => resolve(false), ms))])
}

describe('the app mounts only once its stylesheets are in', () => {
  afterEach(() => {
    document.head.innerHTML = ''
  })

  it('waits for a stylesheet still on its way', async () => {
    const link = document.createElement('link')
    link.rel = 'preload'
    link.setAttribute('as', 'style')
    document.head.appendChild(link)

    const ready = stylesheetsLoaded(document, 60_000)
    expect(await settledWithin(ready, 20)).toBe(false)

    link.dispatchEvent(new Event('error'))
    expect(await settledWithin(ready, 20)).toBe(true)
  })

  it('does not wait for ever on a stylesheet that never arrives', async () => {
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    document.head.appendChild(link)

    expect(await settledWithin(stylesheetsLoaded(document, 30), 200)).toBe(true)
  })
})

describe('the first page', () => {
  it('is ready once the session is settled and the first navigation is done, even if either failed', async () => {
    let finishSession!: () => void
    const session = new Promise<void>((resolve) => (finishSession = resolve))
    const ready = firstPageReady(session, Promise.reject(new Error('navigation failed')), 60_000)

    expect(await settledWithin(ready, 20)).toBe(false)
    finishSession()
    expect(await settledWithin(ready, 20)).toBe(true)
  })

  it('does not keep the page from mounting behind a stalled network', async () => {
    vi.useFakeTimers()
    try {
      const ready = firstPageReady(new Promise(() => {}), new Promise(() => {}), 1000)
      vi.advanceTimersByTime(1000)
      await expect(ready).resolves.toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })
})
