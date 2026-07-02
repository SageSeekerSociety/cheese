// @vitest-environment jsdom
// renderMessage: chat/notification rendering — reference-token chips stay
// clickable, human newlines survive, HTML stays escaped.
import { describe, expect, it } from 'vitest'
import { renderMarkdown, renderPlain } from './renderMessage'

const MAPS = {
  mentionNames: { andyl: 'Andy Liu', 'zhang-heng': '张衡' },
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
