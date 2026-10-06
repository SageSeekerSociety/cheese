import type { Block } from '@/cx_types'

import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { askAnswers, askOptions, replySnippet } from './blockDisplay'
import { loadDocRead } from './docReadLoad'

import { setLocale } from '@/i18n'

// These assertions read the Chinese copy.
beforeEach(() => {
  setLocale('zh-CN')
})

function said(author: string, content: string): Block {
  return {
    id: 'b1',
    conversation_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    created_at: '2026-09-25T00:00:00Z',
  } as Block
}

const maps = { mentionNames: { 'cheese-3fa2': '芝士', lixue: '李雪' }, topicTitles: { t9: '第三节图表' } }

describe('replySnippet', () => {
  // 文档的读法第一次用到才加载；这里说的是加载好以后引用条上的字。
  beforeAll(() => loadDocRead())

  it('引用芝士的话时，引到的是读得到的字，不是 markdown 记号', () => {
    const snippet = replySnippet(said('cheese', '按样本分成了 **A / B / C** 三组，单位统一成 `mg/L`'), maps)
    expect(snippet).toContain('A / B / C')
    expect(snippet).not.toMatch(/\*|`/)
  })

  it('链接只引文字，不引地址', () => {
    const snippet = replySnippet(said('cheese', '见[报告](https://example.com/r)'), maps)
    expect(snippet).toContain('报告')
    expect(snippet).not.toContain('example.com')
  })

  it('开头是列表或代码块的回复，引用也不带记号', () => {
    expect(replySnippet(said('cheese', '- 第一项\n- 第二项'), maps)).not.toMatch(/^-/)
    expect(replySnippet(said('cheese', '```py\nprint(1)\n```'), maps)).not.toContain('```')
    expect(replySnippet(said('cheese', '| 组 | 均值 |\n|---|---|\n| A | 0.42 |'), maps)).toContain('0.42')
  })

  it('人说的话原样显示，所以原样引用', () => {
    expect(replySnippet(said('lixue', '**别动**这一行'), maps)).toContain('**别动**')
  })

  it('@ 人、提话题读成名字，和正文里显示的一样', () => {
    expect(replySnippet(said('wang', '<@cheese-3fa2> 看下 <#t9>'), maps)).toBe('@芝士 看下 #第三节图表')
    expect(replySnippet(said('cheese-3fa2', '<@lixue> 改好了'), maps)).toBe('@李雪 改好了')
  })
})

function asked(meta: Record<string, unknown>): Block {
  return { ...said('cheese', '分页方案选哪个？'), meta } as Block
}

describe('一道选项题读出来是什么', () => {
  it('选项是对象，文字取 text，解释提问方给了才带', () => {
    const opts = askOptions(asked({ options: [{ text: 'cursor', explain: '一条 SQL' }, { text: 'pageStart' }] }))
    expect(opts).toEqual([{ text: 'cursor', explain: '一条 SQL' }, { text: 'pageStart' }])
  })

  it('空选项、或者不是选项题，都不是「一道题」', () => {
    expect(askOptions(asked({ options: [] }))).toBeNull()
    expect(askOptions(asked({}))).toBeNull()
    expect(askOptions(said('cheese', '普通一句话'))).toBeNull()
  })

  it('迁移前的 string[] 不是另一种写法：读不出来，不是被顺手收下', () => {
    expect(askOptions(asked({ options: ['cursor', 'pageStart'] }))).toBeNull()
  })
})

describe('答过这道题的每一句', () => {
  it('还没人答是空的；答过的按先后列出谁说了什么', () => {
    expect(askAnswers(asked({ options: [{ text: 'a' }] }))).toEqual([])
    const answers = askAnswers(
      asked({
        options: [{ text: 'a' }, { text: 'b' }],
        answer_log: [
          { kind: 'option', option: 'a', note: null, by: 'alice', at: '2026-10-05T00:00:00Z', reply_id: 'r1' },
          { kind: 'note', option: null, note: '都不要', by: 'bob', at: '2026-10-05T00:01:00Z', reply_id: 'r2' },
        ],
      })
    )
    expect(answers).toEqual([
      { by: 'alice', text: 'a' },
      { by: 'bob', text: '都不要' },
    ])
  })

  it('早先答的「以上都不是」照样读得出来', () => {
    expect(
      askAnswers(
        asked({
          answer_log: [{ v: 1, kind: 'reject', option: null, note: null, by: 'u', at: null, client_op_id: 'x' }],
        })
      )
    ).toEqual([{ by: 'u', text: '以上都不是' }])
  })
})
