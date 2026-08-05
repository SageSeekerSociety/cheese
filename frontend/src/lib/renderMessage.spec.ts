// @vitest-environment jsdom
// renderMessage: chat/notification rendering — reference-token chips stay
// clickable, human newlines survive, HTML stays escaped.
import type { Block } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { coalesceSplitFencedCodeBlocks, renderMarkdown, renderPlain } from './renderMessage'

const MAPS = {
  // `all`/`here` are the reserved 群播 tokens seeded by ChatPanel.
  mentionNames: {
    andyl: 'Andy Liu',
    'zhang-heng': '张衡',
    all: '所有人',
    here: '在线成员',
  },
  topicTitles: { 'abc12345-0000-0000-0000-000000000000': '搭建推荐算法原型' },
}

describe('renderMarkdown (芝士 replies)', () => {
  it('renders a <@handle> token as a clickable mention chip', () => {
    const html = renderMarkdown('<@andyl> 都办好了', MAPS)
    expect(html).toContain('class="mention"')
    expect(html).toContain('data-handle="andyl"')
    expect(html).toContain('@Andy Liu')
  })

  it('renders a <#topicId> token as a topic chip with its title', () => {
    const html = renderMarkdown(
      '进展见 <#abc12345-0000-0000-0000-000000000000>',
      MAPS,
    )
    expect(html).toContain('data-topic="abc12345-0000-0000-0000-000000000000"')
    expect(html).toContain('#搭建推荐算法原型')
  })

  it('falls back to the raw handle when the roster has no name', () => {
    const html = renderMarkdown('<@ghost> 看下', MAPS)
    expect(html).toContain('data-handle="ghost"')
    expect(html).toContain('@ghost')
  })

  it('renders the 群播 <@all> token as a friendly chip (fusion-design §3)', () => {
    const html = renderMarkdown('<@all> 大家看一下', MAPS)
    expect(html).toContain('class="mention"')
    expect(html).toContain('data-handle="all"')
    expect(html).toContain('@所有人')
  })

  it('renders <@here> as the 在线成员 chip', () => {
    const html = renderMarkdown('<@here> 在的看下', MAPS)
    expect(html).toContain('data-handle="here"')
    expect(html).toContain('@在线成员')
  })

  it('keeps single newlines as line breaks (chat, not strict markdown)', () => {
    const html = renderMarkdown('第一行\n第二行', MAPS)
    expect(html).toContain('<br')
  })

  it('sanitizes script injection', () => {
    const html = renderMarkdown('<script>alert(1)</script>hi', MAPS)
    expect(html).not.toContain('<script')
  })
})

describe('renderPlain (human messages)', () => {
  it('keeps typed newlines as literal \\n for pre-wrap rendering', () => {
    const html = renderPlain('第一行\n第二行', MAPS)
    expect(html).toContain('第一行\n第二行')
  })

  it('renders reference tokens as chips in plain text too', () => {
    const html = renderPlain('<@zhang-heng> 这周过一下', MAPS)
    expect(html).toContain('data-handle="zhang-heng"')
    expect(html).toContain('@张衡')
  })

  it('escapes HTML instead of executing it', () => {
    const html = renderPlain('<b>bold?</b>', MAPS)
    expect(html).not.toContain('<b>')
    expect(html).toContain('&lt;b&gt;')
  })
})

describe('historical fragmented Markdown compatibility', () => {
  function aiBlock(id: string, content: string, turnId = 'e56c632e-9ec3-4ed5-a670-e116299044dd'): Block {
    return {
      id,
      topic_id: 'topic-1',
      kind: 'message',
      author_type: 'ai',
      author: 'cheese',
      content,
      turn_id: turnId,
      created_at: '2026-07-18T17:58:24Z',
    }
  }

  it('coalesces a split fence so its code is visible in one Markdown parse', () => {
    const fragments = [
      aiBlock('open', '  ```python\n'),
      aiBlock('line-1', '  # dogfood loop: accepted on cheesex\n'),
      aiBlock('line-2', '  # self-update on dev: written via the platform\n'),
      aiBlock('close', '  ```\n'),
    ]

    const repaired = coalesceSplitFencedCodeBlocks(fragments)
    expect(repaired).toHaveLength(1)
    expect(repaired[0].id).toBe('open')

    const html = renderMarkdown(repaired[0].content, MAPS)
    expect(html).toContain('<pre><code class="language-python">')
    expect(html).toContain('# dogfood loop: accepted on cheesex')
    expect(html).toContain('# self-update on dev: written via the platform')
  })

  it('leaves ordinary consecutive AI messages independent', () => {
    const blocks = [aiBlock('one', '先确认一下。'), aiBlock('two', '已经完成。')]
    expect(coalesceSplitFencedCodeBlocks(blocks)).toEqual(blocks)
  })

  it('does not join a fence across a human or event boundary', () => {
    const event: Block = {
      ...aiBlock('event', '执行命令'),
      kind: 'event',
    }
    const blocks = [aiBlock('open', '```python\n'), event, aiBlock('close', 'print("late")\n```\n')]
    expect(coalesceSplitFencedCodeBlocks(blocks)).toEqual(blocks)
  })

  it('does not join a fence across turn boundaries', () => {
    const blocks = [
      aiBlock('open', '```python\n'),
      aiBlock('close', 'print("other turn")\n```\n', 'another-turn'),
    ]
    expect(coalesceSplitFencedCodeBlocks(blocks)).toEqual(blocks)
  })
})
