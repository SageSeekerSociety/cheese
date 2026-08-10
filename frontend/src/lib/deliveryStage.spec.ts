import { describe, expect, it } from 'vitest'

import { deliveryNoteTone, deliveryStageOf } from './deliveryStage'

describe('deliveryStageOf', () => {
  it('未合并的卡停在 CI 阶段', () => {
    const stage = deliveryStageOf({ pr_merged_at: null })
    expect(stage.phase).toBe('ci')
    expect(stage.steps.map((s) => s.state)).toEqual(['done', 'active', 'todo', 'todo'])
  })

  it('已合并的卡进入部署阶段，前面的步骤都算完成', () => {
    const stage = deliveryStageOf({ pr_merged_at: '2026-08-10T03:00:00Z' })
    expect(stage.phase).toBe('deploy')
    expect(stage.steps.map((s) => s.state)).toEqual(['done', 'done', 'done', 'active'])
  })

  it('每个阶段都给得出标题和说明（卡面不会出现空白行）', () => {
    for (const merged of [null, '2026-08-10T03:00:00Z']) {
      const stage = deliveryStageOf({ pr_merged_at: merged })
      expect(stage.title.length).toBeGreaterThan(0)
      expect(stage.hint.length).toBeGreaterThan(0)
      expect(stage.steps).toHaveLength(4)
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
    expect(deliveryNoteTone('🚫 PR #12 检查全绿，但 GitHub 拒绝合并（405）')).toBe('error')
  })

  it('普通进度 note 是 info', () => {
    expect(deliveryNoteTone('PR #12 检查全绿，已自动合并，等部署也成功后才归档。')).toBe('info')
  })
})
