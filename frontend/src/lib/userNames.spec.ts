import { describe, expect, it } from 'vitest'

import { secondaryIdentity, userName, userOriginalName } from './agentNames'

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

describe('the secondary identity under a name', () => {
  it('writes the original name and the account when a remark is set', () => {
    expect(secondaryIdentity({ nickname: 'Ada', username: 'ada', remark: 'My colleague' })).toBe('(Ada) · ada')
  })

  it('writes it once when the nickname is the account', () => {
    expect(secondaryIdentity({ nickname: 'ada', username: 'ada', remark: 'My colleague' })).toBe('ada')
  })

  it('writes nothing when there is no remark: the row already carries the name', () => {
    expect(secondaryIdentity({ nickname: 'Ada', username: 'ada' })).toBe('')
    expect(secondaryIdentity(null)).toBe('')
  })

  it('falls back to the account when there is no nickname', () => {
    expect(secondaryIdentity({ username: 'ada', remark: 'My colleague' })).toBe('ada')
  })
})
