// 「AI 队友辅导」在表单与接口之间的翻译：空的那几格要落成 `null` / `[]`（与
// `Teaching.is_empty` 同一个意思），否则「留空」会被读成「写了些空的东西」。
import { describe, expect, it } from 'vitest'

import {
  buildTeaching,
  defaultRolePayload,
  draftFromConfig,
  emptyTeachingDraft,
  isTeachingBlank,
  pickTeachingMode,
} from './teaching'

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
      hintLadder: false,
      outputCheck: false,
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
      hintLadder: false,
      outputCheck: false,
    })
  })

  it('两项保护只跟着「引导为主」交出去；新选上引导为主时默认打开，已经是的不动', () => {
    const guided = { ...emptyTeachingDraft(), mode: 'guide' as const, hintLadder: true, outputCheck: true }
    expect(buildTeaching(guided)).toMatchObject({ hintLadder: true, outputCheck: true })
    expect(buildTeaching({ ...guided, mode: 'example' })).toMatchObject({ hintLadder: false, outputCheck: false })

    const fresh = emptyTeachingDraft()
    pickTeachingMode(fresh, 'guide')
    expect(fresh).toMatchObject({ mode: 'guide', hintLadder: true, outputCheck: true })

    const existing = draftFromConfig({ mode: 'guide' })
    pickTeachingMode(existing, 'guide')
    expect(existing).toMatchObject({ hintLadder: false, outputCheck: false })
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
