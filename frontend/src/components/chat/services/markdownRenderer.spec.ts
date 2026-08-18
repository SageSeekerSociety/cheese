import { describe, expect, it } from 'vitest'

import { MarkdownRenderer, renderMarkdownError } from './markdownRenderer'

describe('MarkdownRenderer', () => {
  // Chat stacks highlighting and KaTeX onto the same instance, so the CJK
  // emphasis rule has to survive that combination — not just its own.
  it('renders bold that closes right before a CJK character', () => {
    const html = new MarkdownRenderer().render('按**执行档案（ExecutionProfile）**解析出模型。')
    expect(html).toContain('<strong>执行档案（ExecutionProfile）</strong>')
  })
})

describe('renderMarkdownError', () => {
  it('renders attacker-controlled Markdown as text-only fallback content', () => {
    const html = renderMarkdownError('<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>')
    const container = document.createElement('div')
    container.innerHTML = html

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(html).toContain('&lt;img')
    expect(html).toContain('&lt;script&gt;')
  })
})
