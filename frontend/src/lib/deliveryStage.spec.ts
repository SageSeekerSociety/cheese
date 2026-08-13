import { describe, expect, it } from 'vitest'

import { deliveryNoteTone, deliveryStageOf } from './deliveryStage'

describe('deliveryStageOf', () => {
  // 自 #206 起 `pr_open` 只有一个阶段：合并即完成。原来那条"已合并→等部署"的
  // 分支不再可达，卡片上也不该再画一个永远不会亮起的「部署」步骤。
  it('pr_open 的卡停在 CI 阶段', () => {
    const stage = deliveryStageOf({ pr_merged_at: null })
    expect(stage.phase).toBe('ci')
    expect(stage.steps.map((s) => s.state)).toEqual(['done', 'active', 'todo'])
  })

  it('链条到「合并进 main」为止，不再承诺部署这一步', () => {
    const stage = deliveryStageOf({ pr_merged_at: null })
    expect(stage.steps.map((s) => s.key)).toEqual(['accepted', 'ci', 'merge'])
  })

  it('给得出标题和说明（卡面不会出现空白行）', () => {
    const stage = deliveryStageOf({ pr_merged_at: null })
    expect(stage.title.length).toBeGreaterThan(0)
    expect(stage.hint.length).toBeGreaterThan(0)
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
