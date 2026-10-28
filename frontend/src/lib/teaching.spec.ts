// 「AI 队友辅导」在表单与接口之间的翻译：空的那几格要落成 `null` / `[]`（与
// `Teaching.is_empty` 同一个意思），否则「留空」会被读成「写了些空的东西」。
import { describe, expect, it } from 'vitest'

import { buildTeaching, defaultRolePayload, draftFromConfig, emptyTeachingDraft, isTeachingBlank } from './teaching'

describe('teaching 表单转换', () => {
  it('接口那一份读成表单那几格，没设的落成空', () => {
    expect(
      draftFromConfig({
        mode: 'spec',
        systemPrompt: '先写需求说明',
        materialIds: [11, 12],
        documentIds: ['0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e'],
        skills: ['wolfram'],
      })
    ).toEqual({
      mode: 'spec',
      systemPrompt: '先写需求说明',
      privatePrompt: '',
      learningGoals: '',
      materialIds: [11, 12],
      documentIds: ['0b6d6a1e-3c1f-4f0a-9a52-6d1f2c3b4a5e'],
      skills: ['wolfram'],
    })
    expect(draftFromConfig(null)).toEqual(emptyTeachingDraft())
  })

  it('交回接口时去掉首尾空白，空的要求落成 null', () => {
    expect(buildTeaching({ ...emptyTeachingDraft(), systemPrompt: '   ' })).toEqual({
      mode: null,
      systemPrompt: null,
      privatePrompt: null,
      learningGoals: [],
      materialIds: [],
      documentIds: [],
      skills: [],
    })
  })

  it('全空才算没说：只选了方式也算这一层说了话', () => {
    expect(isTeachingBlank(null)).toBe(true)
    expect(isTeachingBlank({ mode: null, systemPrompt: ' ', materialIds: [], documentIds: [] })).toBe(true)
    expect(isTeachingBlank({ mode: 'guide' })).toBe(false)
    expect(isTeachingBlank({ materialIds: [3] })).toBe(false)
    expect(isTeachingBlank({ skills: ['wolfram'] })).toBe(false)
  })
})

describe('defaultRolePayload', () => {
  it('选回「继承」且这一层本来没写过：不带这个键（继承上层）', () => {
    expect(defaultRolePayload('', null)).toBeUndefined()
    expect(defaultRolePayload('', undefined)).toBeUndefined()
  })

  it('选回「继承」但这一层本来写过：带空串，明确清掉这一层的覆盖', () => {
    expect(defaultRolePayload('', 'academic-research')).toBe('')
  })

  it('选了某个预设：原样带上它', () => {
    expect(defaultRolePayload('coding-tutor', null)).toBe('coding-tutor')
    expect(defaultRolePayload('coding-tutor', 'academic-research')).toBe('coding-tutor')
  })
})
