// @vitest-environment jsdom
// How a message reads: reference-token chips stay clickable, human newlines
// survive, HTML stays escaped. 芝士's Markdown goes through the reader that
// MarkdownView mounts; a person's words through renderPlain.
import type { Block } from '../cx_types'
import type { RefNames } from './refChip'

import { beforeEach, describe, expect, it } from 'vitest'

import { plainRefs } from './refChip'
import { coalesceSplitFencedCodeBlocks, renderPlain } from './renderMessage'

import { mountMarkdown } from '@/components/panels/doc/blocks/reader'
import { setLocale } from '@/i18n'

// These assertions read the Chinese copy.
beforeEach(() => {
  setLocale('zh-CN')
})

/** 芝士's message as the room draws it. */
function renderMarkdown(text: string, maps: RefNames): string {
  const host = document.createElement('div')
  mountMarkdown(host, text, { as: 'chat', names: maps })
  return host.innerHTML
}

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

describe('芝士 replies', () => {
  it('renders a <@handle> token as a clickable mention chip', () => {
    const html = renderMarkdown('<@andyl> 都办好了', MAPS)
    expect(html).toContain('class="mention"')
    expect(html).toContain('data-handle="andyl"')
    expect(html).toContain('@Andy Liu')
  })

  // 我们自己的工具链到处在写 `<&path:12-30>`——一个认不出来的 token 不会安静地
  // 失败，它原样躺在正文里，把「点开那段代码」变成「读一串尖括号」。
  it('renders a <&path> token as a file chip, line range and all', () => {
    const html = renderMarkdown('看 <&frontend/src/stores/workspace.ts:176-196> 这一段', MAPS)
    expect(html).toContain('data-file="frontend/src/stores/workspace.ts:176-196"')
    expect(html).toContain('workspace.ts:176-196')
    expect(html).not.toContain('&lt;&amp;')
  })

  it('renders a <&path> token with a single line number', () => {
    expect(renderMarkdown('见 <&backend/app/core/db.py:55>', MAPS)).toContain('data-file="backend/app/core/db.py:55"')
  })

  // 点开一个链接不该把人带出这个房间：单页应用回来要整个重载，输入框里没发出
  // 去的字也没了。说明书的链接是最常被点的那种，但这条对 PR、外部资料一样成立。
  it('opens a link in a new tab instead of navigating the room away', () => {
    const html = renderMarkdown('见[成员](https://okcheese.com/docs/members#mention)', MAPS)
    expect(html).toContain('href="https://okcheese.com/docs/members#mention"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('>成员</a>')
  })

  it('renders a <#topicId> token as a topic chip with its title', () => {
    const html = renderMarkdown('进展见 <#abc12345-0000-0000-0000-000000000000>', MAPS)
    expect(html).toContain('data-topic="abc12345-0000-0000-0000-000000000000"')
    expect(html).toContain('#搭建推荐算法原型')
  })

  // 芝士 writes Chinese, and Chinese puts no space after a closing `**` —
  // which is the one position CommonMark refuses to close on. Plain marked
  // leaves the asterisks in the message body.
  it('renders bold that closes right before a CJK character', () => {
    expect(renderMarkdown('按**执行档案（ExecutionProfile）**解析出模型', MAPS)).toContain('<strong>')
    expect(renderMarkdown('**这句话是粗体。**下一句不是', MAPS)).toContain('<strong>')
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

  it('never turns written markup into a script', () => {
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

describe.each([
  ['Markdown', renderMarkdown],
  ['plain text', renderPlain],
] as const)('file references in %s', (_label, renderText) => {
  it.each([
    ['library/design-fit-4096x2304(3).png', 'design-fit-4096x2304(3).png'],
    ['library/报告(2).pdf', '报告(2).pdf'],
    ['src/page(backup).ts:7', 'page(backup).ts:7'],
    ['src/page(2).ts:12-30', 'page(2).ts:12-30'],
  ])('keeps the exact file path and optional line suffix for %s', (path, label) => {
    const host = document.createElement('div')
    host.innerHTML = renderText(`见 <&${path}>：这一份`, MAPS)

    const chip = host.querySelector<HTMLElement>('[data-file]')
    expect(chip?.dataset.file).toBe(path)
    expect(chip?.title).toBe(path)
    expect(chip?.textContent).toBe(label)
    expect(host.textContent?.trimEnd()).toBe(`见 ${label}：这一份`)
  })

  it.each([
    '<&javascript:alert(1)>',
    '<&https://example.com/image(3).png>',
    '<&library/image(3).png?onload=alert(1)>',
    '<&library/image(3).png" onclick="alert(1)>',
    '<&library/image(3).png<script>alert(1)</script>>',
    '<&library/image(3).png<img src=x onerror=alert(1)>>',
  ])('rejects non-file or markup-bearing reference %s', (text) => {
    const host = document.createElement('div')
    host.innerHTML = renderText(text, MAPS)

    expect(host.querySelector('[data-file]')).toBeNull()
    expect(host.querySelector('script, [onclick], [onerror]')).toBeNull()
    if (renderText === renderPlain) expect(host.textContent).toBe(text)
  })
})

describe('plain file-reference labels', () => {
  it.each([
    ['<&library/design-fit-4096x2304(3).png>', 'design-fit-4096x2304(3).png'],
    ['<&library/报告(2).pdf>', '报告(2).pdf'],
    ['<&src/page(backup).ts:7>', 'page(backup).ts:7'],
    ['<&src/page(2).ts:12-30>', 'page(2).ts:12-30'],
  ])('keeps the filename and optional line suffix for %s', (token, label) => {
    expect(plainRefs(`见 ${token}：这一份`, MAPS)).toBe(`见 ${label}：这一份`)
  })
})

describe('historical fragmented Markdown compatibility', () => {
  function aiBlock(id: string, content: string, turnId = 'e56c632e-9ec3-4ed5-a670-e116299044dd'): Block {
    return {
      id,
      topic_id: 'topic-1',
      kind: 'message',
      author_type: 'participant',
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

    const host = document.createElement('div')
    host.innerHTML = renderMarkdown(repaired[0].content, MAPS)
    const code = host.querySelectorAll('pre code')
    expect(code).toHaveLength(1)
    expect(code[0].textContent).toContain('# dogfood loop: accepted on cheesex')
    expect(code[0].textContent).toContain('# self-update on dev: written via the platform')
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
    const blocks = [aiBlock('open', '```python\n'), aiBlock('close', 'print("other turn")\n```\n', 'another-turn')]
    expect(coalesceSplitFencedCodeBlocks(blocks)).toEqual(blocks)
  })
})
