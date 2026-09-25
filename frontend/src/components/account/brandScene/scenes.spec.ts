/** The sign-in pages draw a brand scene each time they are opened — never the
 *  one shown last time — and keep it while the person moves between them. */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SCENE_IDS } from './scenes'

// A fresh module is a fresh page load.
async function openPage() {
  vi.resetModules()
  return (await import('./scenes')).sceneForThisPage
}

beforeEach(() => localStorage.clear())

describe('the scene for this page', () => {
  it('stays the same while the page is open, whatever the next draw would be', async () => {
    const scene = await openPage()
    const first = scene(() => 0)
    expect(scene(() => 0.99)).toBe(first)
  })

  it('is never the one shown on the previous opening', async () => {
    let previous = (await openPage())(() => 0)
    for (let i = 0; i < 20; i++) {
      const next = (await openPage())(() => 0)
      expect(next).not.toBe(previous)
      previous = next
    }
  })

  it('can land on every scene', async () => {
    const seen = new Set<string>()
    for (let i = 0; i < SCENE_IDS.length * 4; i++) {
      seen.add((await openPage())(() => (i % SCENE_IDS.length) / SCENE_IDS.length))
    }
    expect(seen.size).toBe(SCENE_IDS.length)
  })
})
