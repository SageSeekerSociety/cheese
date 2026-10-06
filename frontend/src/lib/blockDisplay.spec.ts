import type { Block } from '@/cx_types'

import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { askAnswered, askOptions, askVersion, replySnippet } from './blockDisplay'
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

describe('答案是日志，末条生效', () => {
  it('还没答过是 null；答过之后取末条', () => {
    expect(askAnswered(asked({ options: [{ text: 'a' }] }))).toBeNull()
    const answered = askAnswered(
      asked({
        options: [{ text: 'a' }, { text: 'b' }],
        answer_log: [
          { v: 1, kind: 'option', option: 'a', note: null, by: 'user-1', at: null, client_op_id: 'migrated' },
          {
            v: 2,
            kind: 'option',
            option: 'b',
            note: null,
            by: 'user-1',
            at: '2026-09-30T00:00:00Z',
            client_op_id: 'op-2',
          },
        ],
      })
    )
    expect(answered).toEqual({ kind: 'option', label: 'b', by: 'user-1' })
  })

  it('更正之后旧版本还看得见 —— 整份日志都在块上', () => {
    const meta = asked({
      options: [{ text: 'a' }, { text: 'b' }],
      answer_log: [
        { v: 1, kind: 'option', option: 'a', note: null, by: 'user-1', at: null, client_op_id: 'migrated' },
        {
          v: 2,
          kind: 'option',
          option: 'b',
          note: null,
          by: 'user-1',
          at: '2026-09-30T00:00:00Z',
          client_op_id: 'op-2',
        },
      ],
    }).meta as { answer_log: { option: string | null }[] }
    expect(meta.answer_log.map((e) => e.option)).toEqual(['a', 'b'])
  })

  it('reject 说「以上都不是」，note 说的是他自己写的那句', () => {
    expect(
      askAnswered(
        asked({
          answer_log: [{ v: 1, kind: 'reject', option: null, note: null, by: 'u', at: null, client_op_id: 'x' }],
        })
      )
    ).toEqual({ kind: 'reject', label: '以上都不是', by: 'u' })
    expect(
      askAnswered(
        asked({
          answer_log: [{ v: 1, kind: 'note', option: null, note: '走第三条路', by: 'u', at: null, client_op_id: 'x' }],
        })
      )
    ).toEqual({ kind: 'note', label: '走第三条路', by: 'u' })
  })

  it('作答要带的版本号就是日志长度：初答 0', () => {
    expect(askVersion(asked({}))).toBe(0)
    expect(
      askVersion(
        asked({
          answer_log: [{ v: 1, kind: 'option', option: 'a', note: null, by: 'u', at: null, client_op_id: 'x' }],
        })
      )
    ).toBe(1)
  })
})
