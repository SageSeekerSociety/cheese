import { describe, expect, it } from 'vitest'

import { renderMarkdownError } from './markdownRenderer'

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
