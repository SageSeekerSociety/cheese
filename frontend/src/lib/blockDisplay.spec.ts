import type { Block, ChecklistMeta, TodoItem } from '@/cx_types'

import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { askAnswers, askClosedByAsker, askOptions, isVideoBlock, replyBlock, replySnippet } from './blockDisplay'
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

describe('视频附件的内联播放入口', () => {
  it.each(['mp4', 'm4v', 'mov', 'webm', 'ogv', 'MP4', 'MOV'])(
    '媒体读取白名单内的 .%s 显示播放器，不依赖上传时的 MIME',
    (suffix) => {
      const block = {
        ...said('alice', `library/clip.${suffix}`),
        kind: 'attachment',
        mime_type: 'application/octet-stream',
      } as Block
      expect(isVideoBlock(block)).toBe(true)
    }
  )

  it.each([
    ['clip.avi', 'video/x-msvideo'],
    ['clip.mkv', 'video/x-matroska'],
    ['clip', 'video/mp4'],
    ['clip.html', 'video/mp4'],
    ['clip.svg', 'video/webm'],
    ['notes.constructor', 'application/octet-stream'],
    ['notes.__proto__', 'application/octet-stream'],
  ])('不在媒体读取白名单的 %s 走下载，即使 MIME 是 %s', (name, mime) => {
    const block = { ...said('alice', `library/${name}`), kind: 'attachment', mime_type: mime } as Block
    expect(isVideoBlock(block)).toBe(false)
  })

  it('只有附件显示播放器，普通消息里的视频路径不播放', () => {
    expect(isVideoBlock(said('alice', 'library/clip.mp4'))).toBe(false)
  })
})

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

  it('父消息不在页里时，引用条读随页带来的那一行', () => {
    const preview = {
      id: 'old',
      kind: 'message',
      author_type: 'participant' as const,
      author: 'lixue',
      content: '报价按 3 万走',
    }
    expect(replySnippet(preview, maps)).toBe('报价按 3 万走')
  })
})

type Step = { subject: string; status: TodoItem['status'] }

const MARK: Record<TodoItem['status'], string> = {
  completed: '✓',
  in_progress: '✱',
  pending: '○',
}

/** 队友的一条步骤清单消息：正文一步一行、记号开头，结构在 `meta.checklist` 里。 */
function listBlock(steps: Step[]): Block {
  return {
    ...said('cheese', steps.map((step) => `${MARK[step.status]} ${step.subject}`).join('\n')),
    meta: {
      checklist: {
        items: steps.map((step, n) => ({ id: String(n + 1), ...step })),
        result: null,
      },
    },
  } as Block
}

describe('步骤清单在摘要里说什么', () => {
  it('念正在做的那一步，不念早已做完的第一步，也不念折成一行的那串记号', () => {
    const block = listBlock([
      { subject: '读 main 源码', status: 'completed' },
      { subject: '改摘要行', status: 'in_progress' },
      { subject: '跑相关检查', status: 'pending' },
    ])
    expect(replySnippet(block, maps, 200)).toBe('✱ 改摘要行')
  })

  it('没有正在做的，念还没做的第一步', () => {
    const block = listBlock([
      { subject: '读 main 源码', status: 'completed' },
      { subject: '改摘要行', status: 'pending' },
      { subject: '跑相关检查', status: 'pending' },
    ])
    expect(replySnippet(block, maps, 200)).toBe('○ 改摘要行')
  })

  it('全做完、写了结果，念结果', () => {
    const block = listBlock([
      { subject: '读 main 源码', status: 'completed' },
      { subject: '改摘要行', status: 'completed' },
    ])
    block.meta = { checklist: { ...(block.meta?.checklist as ChecklistMeta), result: '摘要行改好了' } }
    expect(replySnippet(block, maps, 200)).toBe('✅ 摘要行改好了')
  })

  it('还有没做完的步骤时，结果先不念', () => {
    const block = listBlock([
      { subject: '读 main 源码', status: 'completed' },
      { subject: '改摘要行', status: 'pending' },
    ])
    block.meta = { checklist: { ...(block.meta?.checklist as ChecklistMeta), result: '先写一句' } }
    expect(replySnippet(block, maps, 200)).toBe('○ 改摘要行')
  })

  it('太长了照旧截断', () => {
    const block = listBlock([{ subject: '一步一步地把这件事说清楚', status: 'in_progress' }])
    expect(replySnippet(block, maps, 6)).toBe('✱ 一步一步…')
  })

  it('迁移前那种只有 `true` 的清单，也从正文里念那一步，不念一串记号', () => {
    const block = {
      ...said('lin', '✓ 读代码\n✱ 改摘要行\n○ 回结论'),
      meta: { checklist: true },
    } as Block
    expect(replySnippet(block, maps, 200)).toBe('✱ 改摘要行')
  })
})

describe('支线摘要里那条最后回复', () => {
  it('后端随它带上清单结构时，摘要念的是清单正在做的那一步', () => {
    const message = listBlock([
      { subject: '读 main 源码', status: 'completed' },
      { subject: '改摘要行', status: 'in_progress' },
    ])
    const reply = {
      author: 'cheese',
      content: message.content,
      created_at: message.created_at,
      checklist: message.meta?.checklist as ChecklistMeta,
    }
    expect(replySnippet(replyBlock(reply), maps, 200)).toBe('✱ 改摘要行')
  })

  it('迁移前的清单（后端带来 `true`）也只念一步', () => {
    const reply = {
      author: 'cheese',
      content: '✓ 取一份主干代码\n✓ 定位中间分割线\n✱ 查回复树的加载路径\n○ 在支线里回结论',
      created_at: '2026-10-12T00:00:00Z',
      checklist: true as const,
    }
    expect(replySnippet(replyBlock(reply), maps, 200)).toBe('✱ 查回复树的加载路径')
  })

  it('后端没带结构时，还是按正文说', () => {
    const reply = { author: 'lin', content: '下午三点评审', created_at: '2026-10-12T00:00:00Z' }
    expect(replySnippet(replyBlock(reply), maps, 200)).toBe('下午三点评审')
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
      { by: 'alice', text: 'a', skipped: false },
      { by: 'bob', text: '都不要', skipped: false },
    ])
  })

  it('早先答的「以上都不是」照样读得出来', () => {
    expect(
      askAnswers(
        asked({
          answer_log: [{ v: 1, kind: 'reject', option: null, note: null, by: 'u', at: null, client_op_id: 'x' }],
        })
      )
    ).toEqual([{ by: 'u', text: '以上都不是', skipped: false }])
  })

  it('提问方收起的题不算有人答，也读得出是撤回还是过期', () => {
    for (const kind of ['withdrawn', 'expired'] as const) {
      const block = asked({
        options: [{ text: 'a' }],
        answer_log: [{ kind, option: null, note: null, by: 'cheese', at: '2026-10-05T00:00:00Z', reply_id: null }],
      })
      expect(askAnswers(block)).toEqual([])
      expect(askClosedByAsker(block)).toBe(kind)
    }
    expect(askClosedByAsker(asked({ options: [{ text: 'a' }] }))).toBeNull()
  })
})
