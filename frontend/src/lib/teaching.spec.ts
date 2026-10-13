// 「默认队友」的三种提交口径（#944）。它是发题页、改题页、项目集设置三处共用的判断，
// 所以单拎出来盯：选回「继承」带不带这个键，取决于这一层**本来写没写过**。
import { describe, expect, it } from 'vitest'

import { defaultRolePayload } from './teaching'

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
