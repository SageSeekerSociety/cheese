import { describe, expect, it } from 'vitest'

import { userName, userOriginalName } from './agentNames'

describe('personal names on user-shaped rows', () => {
  it('uses only this row’s private remark and leaves public identity intact', () => {
    const a = { username: 'reader', nickname: 'Public Reader', remark: 'My colleague' }
    const b = { username: 'reader', nickname: 'Public Reader', remark: null }
    expect(userName(a)).toBe('My colleague')
    expect(userOriginalName(a)).toBe('Public Reader')
    expect(userName(b)).toBe('Public Reader')
    expect(a.nickname).toBe('Public Reader')
    a.remark = ''
    expect(userName(a)).toBe('Public Reader')
  })

  it('falls back to account names and tolerates missing users', () => {
    expect(userName({ username: 'reader', nickname: '', remark: null })).toBe('reader')
    expect(userName(null)).toBe('')
    expect(userOriginalName(undefined)).toBe('')
  })
})
