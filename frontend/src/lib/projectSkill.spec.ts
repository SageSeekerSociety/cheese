import { describe, expect, it } from 'vitest'

import { suggestSkillName } from './projectSkill'

const VALID = /^[a-z0-9][a-z0-9-]{1,47}$/

describe('suggestSkillName', () => {
  it('spells an English title as the name', () => {
    expect(suggestSkillName('Release Notes', [])).toBe('release-notes')
  })

  it('gives a title with no English a name the server accepts', () => {
    const name = suggestSkillName('项目周报', [])
    expect(name).toMatch(VALID)
  })

  it('never suggests a name the project already uses', () => {
    const taken = ['skill-1', 'release-notes']
    expect(taken).not.toContain(suggestSkillName('批改作业', taken))
    expect(taken).not.toContain(suggestSkillName('Release Notes', taken))
    expect(suggestSkillName('Release Notes', taken)).toMatch(VALID)
  })

  it('stays within the length the server takes', () => {
    expect(suggestSkillName('a'.repeat(80), [])).toMatch(VALID)
    expect(suggestSkillName('a'.repeat(80), ['a'.repeat(48)])).toMatch(VALID)
  })
})
