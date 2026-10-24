import type { StagedForm } from './taskStages'

import { describe, expect, it } from 'vitest'

import { addStage, moveStage, refusedStageChange, removeStage, rowsAfterStageChange } from './taskStages'

const stage = (name: string, id: string) => ({ id, name, description: '', dueAt: null })

const form = (): StagedForm => ({
  stages: [stage('一', 'a1'), stage('二', 'b2'), stage('三', 'c3')],
  items: [
    { prompt: 'a', type: 'FILE', stage: 'a1' },
    { prompt: 'b', type: 'FILE', stage: 'b2' },
    { prompt: 'c', type: 'TEXT', stage: 'c3' },
  ],
  rubric: [{ item: '正确', points: 10, criteria: '', stage: 'c3' }],
})

describe('发题表单里的阶段', () => {
  it('从不分阶段加第一个阶段，已有的提交项和评分要点都归它', () => {
    const next = addStage({
      stages: [],
      items: [{ prompt: 'a', type: 'FILE', stage: null }],
      rubric: [{ item: '正确', points: 10, criteria: '', stage: null }],
    })

    expect(next.stages).toHaveLength(1)
    const id = next.stages[0].id
    expect(id).toMatch(/^[a-z0-9]+$/)
    expect(next.items[0].stage).toBe(id)
    expect(next.rubric[0].stage).toBe(id)
  })

  it('再加一个阶段，它自带一个要交的文件，id 和别的阶段不同', () => {
    const next = addStage(form())

    expect(next.stages).toHaveLength(4)
    const id = next.stages[3].id
    expect(['a1', 'b2', 'c3']).not.toContain(id)
    expect(next.items.filter((i) => i.stage === id)).toHaveLength(1)
  })

  it('删掉一个阶段，它的提交项和评分要点一起去掉，别的留在原来的阶段下', () => {
    const next = removeStage(form(), 1)

    expect(next.stages.map((s) => s.name)).toEqual(['一', '三'])
    expect(next.items).toEqual([
      { prompt: 'a', type: 'FILE', stage: 'a1' },
      { prompt: 'c', type: 'TEXT', stage: 'c3' },
    ])
    expect(next.rubric[0].stage).toBe('c3')
  })

  it('删掉最后剩下的阶段，题目回到不分阶段，提交项留下', () => {
    const one: StagedForm = {
      stages: [stage('一', 'a1')],
      items: [{ prompt: 'a', type: 'FILE', stage: 'a1' }],
      rubric: [],
    }

    expect(removeStage(one, 0)).toEqual({ stages: [], items: [{ prompt: 'a', type: 'FILE', stage: null }], rubric: [] })
  })

  // The same cases as the backend's `test_stage_rows.py`: the page and the server follow one rule.
  it.each([
    ['last stages removed: kept, unstaged', ['a', 'b'], [], ['a', 'b', null], { kept: [null, null, null], removed: 0 }],
    ['a stage removed: its rows go', ['a', 'b'], ['b'], ['a', 'b'], { kept: ['b'], removed: 1 }],
    ['no stage before: the first new one', [], ['x', 'y'], [null], { kept: ['x'], removed: 0 }],
    ['renamed without its id: a new stage, rows go', ['a'], ['n'], ['a'], { kept: [], removed: 1 }],
    [
      'no stage named: the old first stage, wherever it went',
      ['a', 'b'],
      ['b', 'a'],
      [null],
      { kept: ['a'], removed: 0 },
    ],
    ['several stages to none: kept, unstaged', ['a', 'b', 'c'], [], ['b', 'c'], { kept: [null, null], removed: 0 }],
  ])('%s', (_, oldIds, newIds, refs, want) => {
    const rows = refs.map((stage, i) => ({ prompt: String(i), type: 'FILE' as const, stage }))
    const { kept, removed } = rowsAfterStageChange(rows, oldIds, newIds)
    expect(kept.map((row) => row.stage)).toEqual(want.kept)
    expect(removed).toHaveLength(want.removed)
  })

  // Hand-ins never move between stages: the backend's `HAND_INS` table, case for case.
  it.each([
    ['several stages to none, a hand-in in the second', ['a', 'b'], [], [1], 1],
    ['several stages to none, a hand-in in the first only', ['a', 'b'], [], [0], null],
    ['several stages to none, no hand-ins', ['a', 'b'], [], [], null],
    ['a stage holding a hand-in removed', ['a', 'b'], ['b'], [0], 0],
    ['reordered', ['a', 'b'], ['b', 'a'], [0, 1], null],
    ['no stage before', [], ['x'], [0], null],
  ])('%s', (_, oldIds, newIds, held, refused) => {
    expect(refusedStageChange(oldIds as string[], newIds as string[], new Set(held as number[]))).toBe(refused)
  })

  it('调整先后，提交项和评分要点仍指着各自的阶段', () => {
    const next = moveStage(form(), 2, 1)

    expect(next.stages.map((s) => [s.name, s.id])).toEqual([
      ['一', 'a1'],
      ['三', 'c3'],
      ['二', 'b2'],
    ])
    expect(next.items).toEqual(form().items)
    expect(next.rubric).toEqual(form().rubric)
  })
})
