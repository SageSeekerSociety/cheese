import { describe, expect, it } from 'vitest'

import { MarkdownRenderer, renderMarkdownError } from './markdownRenderer'

describe('MarkdownRenderer', () => {
  // Chat stacks highlighting and KaTeX onto the same instance, so the CJK
  // emphasis rule has to survive that combination — not just its own.
  it('renders bold that closes right before a CJK character', () => {
    const html = new MarkdownRenderer().render('按**执行档案（ExecutionProfile）**解析出模型。')
    expect(html).toContain('<strong>执行档案（ExecutionProfile）</strong>')
  })

  // 回归：代码块的着色靠 Prism 的语法。曾经 Prism 那个 chunk 在模块求值时抛
  // `Prism is not defined`（core 惰性、语法先跑），凡是渲染 Markdown 的路由都会白屏。
  // 没有语法时这块会原样吐出纯文本，`token` 这个类不会出现。
  it('highlights a fenced code block through Prism', () => {
    const html = new MarkdownRenderer().render('```js\nconst answer = 42\n```')
    expect(html).toContain('class="token')
    expect(html).toContain('language-js')
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
