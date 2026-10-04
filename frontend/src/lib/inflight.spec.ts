import { expect, it, vi } from 'vitest'

import { shareInFlight } from './inflight'

it('concurrent callers share one start and get the same result', async () => {
  const start = vi.fn(() => Promise.resolve({ data: [1, 2, 3] }))
  const [a, b, c] = [shareInFlight('k', start), shareInFlight('k', start), shareInFlight('k', start)]
  expect(start).toHaveBeenCalledTimes(1)
  expect(await a).toBe(await b)
  expect(await b).toBe(await c)
})

it('a later call after settling goes to the server again', async () => {
  const start = vi.fn(() => Promise.resolve('v'))
  await shareInFlight('k', start)
  await shareInFlight('k', start)
  expect(start).toHaveBeenCalledTimes(2)
})

it('different keys never share', async () => {
  const start = vi.fn(() => Promise.resolve('v'))
  await Promise.all([shareInFlight('a', start), shareInFlight('b', start)])
  expect(start).toHaveBeenCalledTimes(2)
})

it('a rejected start is cleared so the next caller retries', async () => {
  const boom = vi.fn(() => Promise.reject(new Error('nope')))
  await expect(shareInFlight('k', boom)).rejects.toThrow('nope')
  const ok = vi.fn(() => Promise.resolve('v'))
  expect(await shareInFlight('k', ok)).toBe('v')
  expect(ok).toHaveBeenCalledTimes(1)
})

it('a settled caller leaves room for a fresh in-flight share', async () => {
  let resolve!: (v: string) => void
  const slow = vi.fn(() => new Promise<string>((yes) => (resolve = yes)))
  const first = shareInFlight('k', slow)
  const second = shareInFlight('k', slow)
  expect(slow).toHaveBeenCalledTimes(1)
  resolve('v')
  expect(await first).toBe('v')
  expect(await second).toBe('v')
})
