/** The sign-in pages draw one brand scene at random per visit, and keep it: going
 *  from sign-in to sign-up and back must not swap the picture. */
import { beforeEach, describe, expect, it } from 'vitest'

import { SCENE_IDS, sceneForThisVisit } from './scenes'

beforeEach(() => sessionStorage.clear())

describe('the scene for this visit', () => {
  it('stays the same for the rest of the visit, whatever the next draw would be', () => {
    const first = sceneForThisVisit(() => 0)
    expect(sceneForThisVisit(() => 0.99)).toBe(first)
  })

  it('draws from the whole set on a new visit', () => {
    const seen = new Set<string>()
    SCENE_IDS.forEach((_, i) => {
      sessionStorage.clear()
      seen.add(sceneForThisVisit(() => i / SCENE_IDS.length))
    })
    expect(seen.size).toBe(SCENE_IDS.length)
  })
})
