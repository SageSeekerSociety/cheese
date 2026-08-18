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
  it('空 note 不显示', () => {
    expect(deliveryNoteTone('')).toBeNull()
    expect(deliveryNoteTone('   ')).toBeNull()
  })

  it('后端的三个告警前缀都算 error', () => {
    expect(deliveryNoteTone('⚠️ CI 检查未通过：boom')).toBe('error')
    expect(deliveryNoteTone('❌ 部署失败：boom')).toBe('error')
    expect(deliveryNoteTone('🚫 PR #12 合不进去：GitHub 拒绝合并（405）')).toBe('error')
  })

  it('普通进度 note 是 info', () => {
    expect(deliveryNoteTone('PR #12 检查全绿，已自动合并，等部署也成功后才归档。')).toBe('info')
  })
})
