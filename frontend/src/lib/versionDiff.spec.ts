import { describe, expect, it } from 'vitest'

import { versionDiff } from './versionDiff'

describe('文档两版逐段对比', () => {
  it('改了一段：配成一对，标出改了的字', () => {
    const d = versionDiff('甲\n乙是旧的\n丙', '甲\n乙是新的\n丙')
    expect(d.changed).toBe(1)
    const row = d.rows.find((r) => r.kind === 'changed')
    expect(row && row.kind === 'changed' && row.segments.some((s) => s.kind === 'add' && s.text.includes('新'))).toBe(
      true
    )
  })

  it('加一段、删一段分开数', () => {
    const d = versionDiff('a\nb\nc', 'a\nc\nd')
    expect(d.removed + d.changed).toBeGreaterThan(0)
    expect(d.rows.some((r) => r.kind === 'add' || r.kind === 'changed')).toBe(true)
  })

  it('没改的长段折起来，改动两边各留两段', () => {
    const before = Array.from({ length: 20 }, (_, i) => `第${i}段`).join('\n')
    const after = before.replace('第10段', '第10段改')
    const d = versionDiff(before, after)
    expect(d.rows[0]).toEqual({ kind: 'skip', count: 8 })
    expect(d.rows.at(-1)).toEqual({ kind: 'skip', count: 7 })
    expect(d.rows.filter((r) => r.kind === 'same')).toHaveLength(4)
  })

  it('第一版：整篇算新加的', () => {
    const d = versionDiff(null, 'x\ny')
    expect(d.added).toBe(2)
    expect(d.rows.every((r) => r.kind === 'add')).toBe(true)
  })

  it('两版一样：没有改动', () => {
    const d = versionDiff('a\nb', 'a\nb')
    expect(d.added + d.removed + d.changed).toBe(0)
  })
})
