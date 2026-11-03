import { describe, expect, it } from 'vitest'

import { BLANK_LINE, changeRuns, versionDiff } from './versionDiff'

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

  it('段与段之间的空行不算一段', () => {
    const d = versionDiff('a\n\nb', 'a\n\nb\n\nc')
    expect(d.added).toBe(1)
    expect(versionDiff(null, 'a\n\nb').added).toBe(2)
  })
})

describe('整篇对比（预览里的对比态）', () => {
  it('collapse: false 留着每一段没改的，不折成「N 段未改」', () => {
    const before = ['一', '二', '三', '四', '五', '六', '七'].join('\n')
    const after = ['一', '二', '三', '四', '五', '六', '七改'].join('\n')
    const diff = versionDiff(before, after, { collapse: false })
    expect(diff.rows.some((r) => r.kind === 'skip')).toBe(false)
    expect(diff.rows).toHaveLength(7)
  })

  it('「几处」按连着的改动算：相邻两段一起改是一处，隔开的是两处', () => {
    const before = ['a', 'b', 'c', 'd', 'e'].join('\n')
    const after = ['a', 'B', 'C', 'd', 'E'].join('\n')
    expect(changeRuns(versionDiff(before, after, { collapse: false }).rows)).toBe(2)
  })

  it('只动了空行（Markdown 换成占位字符的那种）不算一处', () => {
    const before = ['# 标题', BLANK_LINE, '正文'].join('\n')
    const after = ['# 标题', BLANK_LINE, BLANK_LINE, '正文'].join('\n')
    const rows = versionDiff(before, after, { collapse: false }).rows
    expect(rows.some((r) => r.kind !== 'same')).toBe(true)
    expect(changeRuns(rows)).toBe(0)
  })
})
