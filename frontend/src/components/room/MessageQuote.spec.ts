/**
 * 对话里挂着的那一份引用。
 *
 * 两种引用共用一个框：文字引用要看得见引的是哪一段，位置引用要看得见指在第几页的哪儿。
 * 两者都不是装饰——受话人靠它回原文件里找那一处。
 */
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import MessageQuote from './MessageQuote.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

it('整页文字引用：报出页码、文件身份和原文', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'slide-page' as const,
        path: 'deck.pptx',
        source: 'committed' as const,
        version: 'v7',
        task_id: null,
        page: 3,
        text: '这一段不对',
      },
    },
  })

  expect(container.textContent).toContain('引用第 3 页文字')
  expect(container.textContent).toContain('deck.pptx')
  expect(container.textContent).toContain('已提交文件')
  expect(container.textContent).toContain('v7')
  expect(container.textContent).toContain('这一段不对')
})

it('页上一点：报出第几页、指在哪儿，不装作有原文', () => {
  const { container } = render(MessageQuote, {
    props: {
      quote: {
        kind: 'page-pin' as const,
        path: 'deck.pdf',
        source: 'live' as const,
        version: 'v7',
        task_id: null,
        page: 5,
        x: 0.42,
        y: 0.17,
      },
    },
  })

  expect(container.textContent).toContain('指了第 5 页的一处')
  expect(container.textContent).toContain('从左 42%、从上 17%')
  expect(container.textContent).toContain('当前工作文件')
  expect(container.querySelector('.message-quote__text')).toBeNull()
})
