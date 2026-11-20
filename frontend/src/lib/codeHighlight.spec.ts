import { describe, expect, it } from 'vitest'

import { highlightLines, sourceOfLines, splitLines } from './codeHighlight'

describe('splitLines', () => {
  it('以换行结尾的文件不多出一行空行', () => {
    expect(splitLines('a\nb\n')).toEqual(['a', 'b'])
    expect(splitLines('a\r\nb')).toEqual(['a', 'b'])
    expect(splitLines('')).toEqual([''])
  })
})

describe('highlightLines', () => {
  it('一个跨行的记号在每一行重新开一次，每一行的 HTML 自己配平', () => {
    const lines = highlightLines('x = """第一行\n第二行"""\ny = 1', 'python')
    expect(lines).toHaveLength(3)
    for (const html of lines) {
      expect(html.split('<span').length).toBe(html.split('</span>').length)
    }
    expect(lines[1]).toContain('hljs-string')
    expect(lines[1]).toContain('第二行')
  })

  it('认不出的语言按纯文本摆，并且转义', () => {
    expect(highlightLines('<b>&</b>', 'no-such-language')).toEqual(['&lt;b&gt;&amp;&lt;/b&gt;'])
    expect(highlightLines('a <b>', null)).toEqual(['a &lt;b&gt;'])
  })

  it('高亮出来的字也转义', () => {
    expect(highlightLines('const s = "<img>"', 'typescript')[0]).toContain('&lt;img&gt;')
    expect(highlightLines('const s = "<img>"', 'typescript')[0]).not.toContain('<img>')
  })
})

describe('sourceOfLines', () => {
  it('取第几行到第几行的原文，含两头', () => {
    expect(sourceOfLines('a\nb\nc\nd\n', 2, 3)).toBe('b\nc')
  })
})
