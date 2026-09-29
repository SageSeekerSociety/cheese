import type { Scene } from './demoScene'

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

// 右侧停在哪一格由剧本一步一句地说，格子里的东西写一次就留在那儿。
describe('the right-hand panel', () => {
  const changes = { files: [{ path: 'README.md', status: 'added' as const, diff: ['+# 课程资料'] }] }

  function scene(steps: Scene['steps']): Scene {
    return {
      title: 't',
      project: 'p',
      topic: '话题',
      machine: '',
      people: { wang: { name: '王长鑫' }, cheese: { name: '芝士', agent: true } },
      steps,
    }
  }

  it('stays on 现场 when the step does not say anything', () => {
    const s = scene([{ label: '一步', events: [] }])
    expect(frameAt(s, 0, 0).panel).toBe('site')
    expect(frameAt(s, 0, 0).changes).toBeNull()
  })

  it('shows the tab a step declares, and keeps its content for the steps after it', () => {
    const s = scene([
      { label: '一', events: [] },
      { label: '二', panel: 'changes', changes, events: [] },
      { label: '三', panel: 'changes', events: [] },
      { label: '四', events: [] },
    ])
    expect(frameAt(s, 1, 0).panel).toBe('changes')
    expect(frameAt(s, 1, 0).changes?.files[0].path).toBe('README.md')
    // 第三步没再抄一遍那份 diff，重放出来的还是它，选的也还是那一格。
    expect(frameAt(s, 2, 0).changes?.files[0].path).toBe('README.md')
    expect(frameAt(s, 2, 0).panel).toBe('changes')
    // 第四步不说，就回到现场——「别的剧本一步都不改」靠的就是这一条。
    expect(frameAt(s, 3, 0).panel).toBe('site')
  })

  it('refuses a step that stops on a tab nothing ever fills', () => {
    const s = scene([
      { label: '一', events: [] },
      { label: '二', panel: 'changes', events: [] },
      { label: '三', panel: 'preview', events: [] },
      { label: '四', changes: { files: [] }, panel: 'changes', events: [] },
    ])
    expect(checkScene(s)).toEqual([
      'step 2「二」: panel is changes, but no step declares changes',
      'step 3「三」: panel is preview, but no step declares preview',
      'step 4「四」: changes has no files',
    ])
  })
})
