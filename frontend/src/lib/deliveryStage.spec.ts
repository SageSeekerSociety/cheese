import { describe, expect, it } from 'vitest'

import { deliveryNoteTone, deliveryStageOf } from './deliveryStage'

const CHECKS = [
  { key: 'accepted', label: '已采纳', state: 'done' as const },
  { key: 'checks', label: '检查', state: 'active' as const },
  { key: 'merge', label: '合并进 main', state: 'todo' as const },
]

const NO_CHECKS = [
  { key: 'accepted', label: '已采纳', state: 'done' as const },
  { key: 'merge', label: '合并进 main', state: 'active' as const },
]

describe('deliveryStageOf', () => {
  // 步骤由后端下发（哪些步骤存在取决于项目的 forge，浏览器看不见），这里只负责
  // 给它们配文案。原来那套 `pr_merged_at` 推导已经删掉，不是搬走。
  it('等检查时的文案指向检查', () => {
    const stage = deliveryStageOf({ stages: CHECKS })
    expect(stage?.title).toContain('检查')
    expect(stage?.steps).toHaveLength(3)
  })

  it('没有外部检查的项目只等合并，文案如实说明', () => {
    const stage = deliveryStageOf({ stages: NO_CHECKS })
    expect(stage?.title).toContain('合并')
    expect(stage?.hint).toContain('没有外部检查')
    expect(stage?.steps.map((s) => s.key)).toEqual(['accepted', 'merge'])
  })

  it('没有在飞的机器动作时不渲染阶段条', () => {
    expect(deliveryStageOf({ stages: [] })).toBeNull()
  })

  it('永远给得出标题和说明（卡面不会出现空白行）', () => {
    for (const stages of [CHECKS, NO_CHECKS]) {
      const stage = deliveryStageOf({ stages })
      expect(stage!.title.length).toBeGreaterThan(0)
      expect(stage!.hint.length).toBeGreaterThan(0)
    }
  })
})

describe('deliveryNoteTone', () => {
  const card = (note: string, note_level: 'error' | 'info' | null) => ({ note, note_level })

  it('空 note 不显示，哪怕后端给了码', () => {
    expect(deliveryNoteTone(card('', 'error'))).toBeNull()
    expect(deliveryNoteTone(card('   ', 'error'))).toBeNull()
  })

  it('停住了的卡是 error', () => {
    expect(deliveryNoteTone(card('⚠️ CI 检查未通过：boom', 'error'))).toBe('error')
    // 这一条正是过去漏掉的：`🌿` 不在浏览器那份写死的 emoji 列表里，于是「分叉了、
    // 等人动手」和「还在等检查」渲染成同一个颜色。分级挪到后端之后它自己就对了。
    expect(deliveryNoteTone(card('🌿 本地分支与 PR 分支已分叉', 'error'))).toBe('error')
  })

  it('还在走的 note 是 info', () => {
    expect(deliveryNoteTone(card('PR #12 检查全绿，已自动合并', 'info'))).toBe('info')
  })

  it('不认识的码退回 info，而不是去读文案开头那个字符', () => {
    // 前端不再有「哪些 emoji 算严重」这份知识 —— 这正是重构要达到的效果。
    expect(deliveryNoteTone(card('❌ 部署失败：boom', null))).toBe('info')
  })
})
