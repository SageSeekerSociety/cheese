import { describe, expect, it } from 'vitest'

import { checkScene, frameAt, stepDuration } from './demoScene'
import { SCENES } from './scenes'

describe.each(Object.entries(SCENES))('scene %s', (_, scene) => {
  it('has no broken events', () => {
    expect(checkScene(scene)).toEqual([])
  })

  it('ends with every turn finished', () => {
    const last = scene.steps.length - 1
    const end = frameAt(scene, last, stepDuration(scene.steps[last]))
    expect(end.running).toEqual({})
  })

  // 一步放下去只往后长：已经在的行不挪位置（步骤清单、按钮卡可以原地改内容）。
  it('only grows while a step plays', () => {
    const ids = (rows: { id: string }[]) => rows.map((r) => r.id)
    scene.steps.forEach((s, i) => {
      const start = frameAt(scene, i, 0)
      const end = frameAt(scene, i, stepDuration(s))
      expect(ids(end.chat).slice(0, start.chat.length)).toEqual(ids(start.chat))
      expect(end.site.slice(0, start.site.length)).toEqual(start.site)
    })
  })
})

describe('frameAt', () => {
  const scene = SCENES.seats

  it('replays to the same frame however it got there', () => {
    const direct = frameAt(scene, 3, 1200)
    frameAt(scene, 5, 0)
    expect(frameAt(scene, 3, 1200)).toEqual(direct)
  })

  it('counts a turn as running between its start and its end', () => {
    const mid = frameAt(scene, 2, 0)
    expect(Object.keys(mid.running).sort()).toEqual(['t1', 't2'])
    expect(mid.runningWho).toEqual(['cheese', 'cheese-k'])
  })

  it('files narration as a progress event so the site shows it as speech', () => {
    const f = frameAt(scene, 2, 900)
    const note = f.site.find((b) => b.meta?.progress === true)
    expect(note?.author).toBe('cheese')
    expect(note?.turn_id).toBe('t1')
  })
})
