import type { Block } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { replySnippet } from './blockDisplay'

function said(author: string, content: string): Block {
  return {
    id: 'b1',
    topic_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    created_at: '2026-09-25T00:00:00Z',
  } as Block
}

describe('replySnippet', () => {
  it('引用芝士的话时，引到的是读得到的字，不是 markdown 记号', () => {
    const snippet = replySnippet(said('cheese', '按样本分成了 **A / B / C** 三组，单位统一成 `mg/L`'))
    expect(snippet).toContain('A / B / C')
    expect(snippet).not.toMatch(/\*|`/)
  })

  it('链接只引文字，不引地址', () => {
    const snippet = replySnippet(said('cheese', '见[报告](https://example.com/r)'))
    expect(snippet).toContain('报告')
    expect(snippet).not.toContain('example.com')
  })

  it('开头是列表或代码块的回复，引用也不带记号', () => {
    expect(replySnippet(said('cheese', '- 第一项\n- 第二项'))).not.toMatch(/^-/)
    expect(replySnippet(said('cheese', '```py\nprint(1)\n```'))).not.toContain('```')
    expect(replySnippet(said('cheese', '| 组 | 均值 |\n|---|---|\n| A | 0.42 |'))).toContain('0.42')
  })

  it('人说的话原样显示，所以原样引用', () => {
    expect(replySnippet(said('lixue', '**别动**这一行'))).toContain('**别动**')
  })
})
