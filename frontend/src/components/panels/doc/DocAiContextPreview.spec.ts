import type { Component } from 'vue'
import type { DocAiDisplayContext } from '../../../lib/docAiTypes'

import { cleanup, render } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import DocAiContextPreview from './DocAiContextPreview.vue'

afterEach(cleanup)
it('renders the frozen question and raw original as text, preserving markup without executing it', () => {
  const question = '<img src=x onerror=alert(1)> original question'
  const original = '\uFEFF😀重复 **原文**\r\n<script>bad()</script>'
  const context: DocAiDisplayContext = { state: 'verified', question, original, scope: 'selection', baseVersion: 4 }
  const { container } = render(DocAiContextPreview as Component, { props: { context } })
  const texts = Array.from(container.querySelectorAll('pre'), (node) => node.textContent)
  expect(texts).toEqual([question, original])
  expect(container.querySelector('img,script,strong')).toBeNull()
})
it('removes the previous original when the next context is invalid or unavailable', async () => {
  const { container, rerender } = render(DocAiContextPreview as Component, {
    props: { context: { state: 'verified', original: 'old private original', scope: 'selection', baseVersion: 4 } },
  })
  expect(container.textContent).toContain('old private original')
  await rerender({ context: { state: 'invalid', question: 'next question' } })
  expect(container.textContent).not.toContain('old private original')
  expect(container.querySelector('pre')?.textContent).toBe('next question')
  await rerender({ context: undefined })
  expect(container.querySelector('pre')).toBeNull()
})
