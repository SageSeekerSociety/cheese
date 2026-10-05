// 文档两版按段比：改了几个字的一段是「改了的一段」，只标出改的那几个字；整段新加或
// 整段删掉的各算各的；没改的段不列出来。
import { describe, expect, it } from 'vitest'

import { compareTexts, inlineDiff, paragraphDiff } from './paragraphDiff'

const DIFF = [
  '--- a/报告.docx',
  '+++ b/报告.docx',
  '@@ -1,4 +1,4 @@',
  ' 一、项目背景',
  '-实验在两个班级中进行，共收集问卷 86 份。',
  '+实验在三个班级中进行，共收集问卷 142 份。',
  ' 二、方法',
  '-由于时间限制，本次实验未设置对照组。',
  '+评审组建议补充的样本偏差讨论见附录 C。',
  '+三个班级的考核方式一致。',
].join('\n')

describe('文档两版按段比', () => {
  it('改了几个字的一段只标出改的字，其余照原样', () => {
    const { paragraphs, changed } = paragraphDiff(DIFF)
    expect(changed).toBeGreaterThanOrEqual(1)
    const first = paragraphs[0]
    expect(first.kind).toBe('changed')
    if (first.kind !== 'changed') return
    const removed = first.segments.filter((s) => s.kind === 'del').map((s) => s.text)
    const added = first.segments.filter((s) => s.kind === 'add').map((s) => s.text)
    expect(removed.join('')).toContain('两')
    expect(added.join('')).toContain('三')
    expect(removed.join('')).not.toContain('班级')
    // 两边拼回去各是原来那一段。
    const before = first.segments
      .filter((s) => s.kind !== 'add')
      .map((s) => s.text)
      .join('')
    const after = first.segments
      .filter((s) => s.kind !== 'del')
      .map((s) => s.text)
      .join('')
    expect(before).toBe('实验在两个班级中进行，共收集问卷 86 份。')
    expect(after).toBe('实验在三个班级中进行，共收集问卷 142 份。')
  })

  it('多出来的一段算新增，没改的段不列出来', () => {
    const { paragraphs, added, removed } = paragraphDiff(DIFF)
    expect(added).toBe(1)
    expect(removed).toBe(0)
    expect(paragraphs.some((p) => p.kind === 'added' && p.text === '三个班级的考核方式一致。')).toBe(true)
    const shown = JSON.stringify(paragraphs)
    expect(shown).not.toContain('一、项目背景')
    expect(shown).not.toContain('二、方法')
  })

  it('只删不加的一段算删除', () => {
    const { paragraphs, removed } = paragraphDiff('@@ -1,2 +1,1 @@\n 留着\n-删掉的这一段')
    expect(removed).toBe(1)
    expect(paragraphs).toEqual([{ kind: 'removed', at: 2, text: '删掉的这一段' }])
  })

  it('英文按词比，不把一个词拆成字母', () => {
    const segments = inlineDiff('the quick fox', 'the slow fox')
    expect(segments.filter((s) => s.kind === 'del').map((s) => s.text)).toEqual(['quick'])
    expect(segments.filter((s) => s.kind === 'add').map((s) => s.text)).toEqual(['slow'])
  })
})

describe('compareTexts', () => {
  it('pairs an edited paragraph and lists a new one, leaving unchanged ones out', () => {
    const before = '# 目标\n\n先做导出。\n\n不变的一段'
    const after = '# 目标\n\n先做导入。\n\n不变的一段\n\n新加的一段'
    const diff = compareTexts(before, after)
    expect(diff.changed).toBe(1)
    expect(diff.added).toBe(1)
    expect(diff.removed).toBe(0)
    const changed = diff.paragraphs.find((p) => p.kind === 'changed')
    expect(
      changed && changed.kind === 'changed' && changed.segments.filter((s) => s.kind === 'add').map((s) => s.text)
    ).toEqual(['入'])
  })

  it('treats everything as new when the document started empty', () => {
    const diff = compareTexts('', '第一段\n\n第二段')
    expect(diff.added).toBe(2)
    expect(diff.changed).toBe(0)
  })
})
