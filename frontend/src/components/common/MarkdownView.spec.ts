// 对话里的一条，读法还在路上时先照原文画出来：空着的话，读法一到整列消息一齐撑高、往下跳。
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 读法一直不到：看的就是它到之前的那一刻。
vi.mock('@/components/panels/doc/blocks/reader', () => new Promise(() => {}))

import MarkdownView from './MarkdownView.vue'

const names = { mentionNames: { bob: 'Bob' }, topicTitles: {} }

describe('MarkdownView before its reader arrives', () => {
  it('shows a chat message as written, references as chips', () => {
    const { container } = render(MarkdownView, { props: { source: '<@bob> 看一下\n第二行', as: 'chat', names } })
    expect(container.textContent).toContain('看一下\n第二行')
    expect(container.querySelector('[data-handle="bob"]')?.textContent).toContain('Bob')
  })

  it('leaves a document blank rather than showing its Markdown source', () => {
    const { container } = render(MarkdownView, { props: { source: '# 标题', as: 'doc' } })
    expect(container.textContent).toBe('')
  })
})
