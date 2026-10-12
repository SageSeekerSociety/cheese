// 名册 → 正文 chip 前那张脸要的那张表（lib/refChip 的 RefNames.mentionIdentities）。
// 各层都从这一处建表（useDocPeople、PanelDocView、DocSurface），所以这里钉住它的形状。
import { describe, expect, it } from 'vitest'

import { identitiesFrom } from './personIdentity'

describe('identitiesFrom', () => {
  it('keeps the avatar address and teammate flag for each handle', () => {
    expect(
      identitiesFrom([
        { handle: 'alice', avatar: 'https://x/avatars/1', agent: false },
        { handle: 'cheese-t1', avatar: null, agent: true },
      ])
    ).toEqual({
      alice: { avatar: 'https://x/avatars/1', agent: false },
      'cheese-t1': { avatar: null, agent: true },
    })
  })

  it('reads a missing avatar or flag as "no picture, a person"', () => {
    expect(identitiesFrom([{ handle: 'bob' }])).toEqual({ bob: { avatar: null, agent: false } })
  })

  it('ignores whatever else the roster row carries', () => {
    const rows = [{ handle: 'erin', avatar: null, agent: false, label: 'Erin', external: true, sub: '@erin' }]
    expect(identitiesFrom(rows)).toEqual({ erin: { avatar: null, agent: false } })
  })

  it('is empty for an empty roster', () => {
    expect(identitiesFrom([])).toEqual({})
  })
})
