// 同一段的两版：只标出改了的字，其余照原样；英文按词，中文按字。
import { describe, expect, it } from 'vitest'

import { inlineDiff } from './paragraphDiff'

describe('同一段的两版', () => {
  it('改了几个字的一段只标出改的字，两边拼回去各是原来那一段', () => {
    const before = '实验在两个班级中进行，共收集问卷 86 份。'
    const after = '实验在三个班级中进行，共收集问卷 142 份。'
    const segments = inlineDiff(before, after)
    const removed = segments.filter((s) => s.kind === 'del').map((s) => s.text)
    const added = segments.filter((s) => s.kind === 'add').map((s) => s.text)
    expect(removed.join('')).toContain('两')
    expect(added.join('')).toContain('三')
    expect(removed.join('')).not.toContain('班级')
    expect(
      segments
        .filter((s) => s.kind !== 'add')
        .map((s) => s.text)
        .join('')
    ).toBe(before)
    expect(
      segments
        .filter((s) => s.kind !== 'del')
        .map((s) => s.text)
        .join('')
    ).toBe(after)
  })

  it('英文按词比，不把一个词拆成字母', () => {
    const segments = inlineDiff('the quick fox', 'the slow fox')
    expect(segments.filter((s) => s.kind === 'del').map((s) => s.text)).toEqual(['quick'])
    expect(segments.filter((s) => s.kind === 'add').map((s) => s.text)).toEqual(['slow'])
  })
})
