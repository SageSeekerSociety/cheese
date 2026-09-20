import { describe, expect, it } from 'vitest'

import { expandMentions } from './expandMentions'

// A roster shaped like the one that broke: a handle that is a prefix of another
// handle, a display name that is a prefix of another display name, and rows
// where the name and the handle are the same string.
const ROSTER = [
  { handle: 'andy', label: 'andy' },
  { handle: 'andylizf', label: 'andylizf' },
  { handle: 'n1ctheboy', label: '李甘-nictheboy' },
  { handle: 'ligan', label: '李甘' },
  { handle: 'cheese-b2813aef8c96', label: '芝士' },
]

describe('expandMentions', () => {
  it('resolves a handle whose prefix is another member', () => {
    expect(expandMentions('比如我现在@andylizf，就会被识别成andy', ROSTER)).toBe(
      '比如我现在<@andylizf>，就会被识别成andy'
    )
  })

  it('resolves a display name whose prefix is another member', () => {
    expect(expandMentions('请 @李甘-nictheboy 看一下', ROSTER)).toBe('请 <@n1ctheboy> 看一下')
  })

  it('still resolves the shorter member on its own', () => {
    expect(expandMentions('@andy 你看看', ROSTER)).toBe('<@andy> 你看看')
    expect(expandMentions('@李甘 你看看', ROSTER)).toBe('<@ligan> 你看看')
  })

  it('leaves a name that only starts like a member alone', () => {
    expect(expandMentions('@andyzzz 是别人', ROSTER)).toBe('@andyzzz 是别人')
  })

  it('resolves a CJK name that runs straight into the next word', () => {
    expect(expandMentions('@李甘来负责', ROSTER)).toBe('<@ligan>来负责')
  })

  it('resolves each of several mentions once', () => {
    expect(expandMentions('@andy 和 @andylizf 都看看', ROSTER)).toBe('<@andy> 和 <@andylizf> 都看看')
  })

  it('leaves an already-canonical token untouched', () => {
    expect(expandMentions('<@andylizf> 你好', ROSTER)).toBe('<@andylizf> 你好')
  })

  it('maps a mis-cased name or handle to the canonical lowercase token', () => {
    expect(expandMentions('@AndyLizf 你好', ROSTER)).toBe('<@andylizf> 你好')
  })

  it('expands the broadcast literals', () => {
    expect(expandMentions('@all 开会', ROSTER)).toBe('<@all> 开会')
    expect(expandMentions('@here 开会', ROSTER)).toBe('<@here> 开会')
  })

  it('expands a topic title to its id token', () => {
    const topics = [{ id: 't-1', title: '分页调研' }]
    expect(expandMentions('见 @分页调研', ROSTER, topics)).toBe('见 <#t-1>')
  })

  it('prefers the longer of two overlapping topic titles', () => {
    const topics = [
      { id: 't-1', title: '分页' },
      { id: 't-2', title: '分页调研' },
    ]
    expect(expandMentions('见 @分页调研', ROSTER, topics)).toBe('见 <#t-2>')
  })

  it('falls back to the handle when a member has no display name', () => {
    expect(expandMentions('@bare 你好', [{ handle: 'bare', label: null }])).toBe('<@bare> 你好')
  })

  it('does not let an empty name swallow every @', () => {
    expect(expandMentions('a@b @andy', [{ handle: 'andy', label: '' }])).toBe('a@b <@andy>')
  })
})
