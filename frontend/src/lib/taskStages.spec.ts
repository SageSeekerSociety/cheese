import type { StagedForm } from './taskStages'

import { describe, expect, it } from 'vitest'

import { addStage, moveStage, removeStage } from './taskStages'

const stage = (name: string, previousIndex: number | null = null) => ({
  name,
  description: '',
  dueAt: null,
  previousIndex,
})

const form = (): StagedForm => ({
  stages: [stage('一', 0), stage('二', 1), stage('三', 2)],
  items: [
    { prompt: 'a', type: 'FILE', stage: 0 },
    { prompt: 'b', type: 'FILE', stage: 1 },
    { prompt: 'c', type: 'TEXT', stage: 2 },
  ],
  rubric: [{ item: '正确', points: 10, criteria: '', stage: 2 }],
})

describe('发题表单里的阶段', () => {
  it('从不分阶段加第一个阶段，已有的提交项和评分要点都归它', () => {
    const next = addStage({ stages: [], items: [{ prompt: 'a', type: 'FILE' }], rubric: [] })

    expect(next.stages).toHaveLength(1)
    expect(next.items).toEqual([{ prompt: 'a', type: 'FILE' }])
  })

  it('再加一个阶段，它自带一个要交的文件', () => {
    const next = addStage(form())

    expect(next.stages).toHaveLength(4)
    expect(next.items.filter((i) => i.stage === 3)).toHaveLength(1)
  })

  it('删掉一个阶段，它的提交项和评分要点一起去掉，后面的往前挪', () => {
    const next = removeStage(form(), 1)

    expect(next.stages.map((s) => s.name)).toEqual(['一', '三'])
    expect(next.items).toEqual([
      { prompt: 'a', type: 'FILE', stage: 0 },
      { prompt: 'c', type: 'TEXT', stage: 1 },
    ])
    expect(next.rubric[0].stage).toBe(1)
  })

  it('删掉最后剩下的阶段，题目回到不分阶段，提交项留下', () => {
    const one = { stages: [stage('一', 0)], items: [{ prompt: 'a', type: 'FILE' as const, stage: 0 }], rubric: [] }

    expect(removeStage(one, 0)).toEqual({ ...one, stages: [] })
  })

  it('调整先后，阶段的提交项和评分要点跟着走，它原来的位置也跟着走', () => {
    const next = moveStage(form(), 2, 1)

    expect(next.stages.map((s) => [s.name, s.previousIndex])).toEqual([
      ['一', 0],
      ['三', 2],
      ['二', 1],
    ])
    expect(next.items.find((i) => i.prompt === 'c')?.stage).toBe(1)
    expect(next.items.find((i) => i.prompt === 'b')?.stage).toBe(2)
    expect(next.rubric[0].stage).toBe(1)
  })
})
