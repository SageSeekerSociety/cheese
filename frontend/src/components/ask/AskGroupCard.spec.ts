// 顶上那排题签：一道题一枚，写的是提问时给的题签，没给就写题号。
//
// 题号只写一次。早先这里画了两遍——一枚是序号、一枚是题签的回退值，两道题就长成
// 「1 1 2 2」。答过的题在前面打勾，题号（或题签）不被勾替换掉。
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AskGroupCard from './AskGroupCard.vue'

import i18n, { setLocale } from '@/i18n'

function question(id: string, meta: Record<string, unknown> = {}): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: `第 ${id} 道题的内容`,
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked: [],
      asked_all: true,
      notice_id: 'notice-1',
      options: [{ text: '甲' }, { text: '乙' }],
      answer_log: [],
      ...meta,
    },
  } as unknown as Block
}

const vuetify = createVuetify({ components, directives })

function renderCard(blocks: Block[]) {
  return render(AskGroupCard as never, {
    props: { blocks, viewer: 'u1', names: {} },
    global: { plugins: [vuetify, i18n] },
  })
}

function tabTexts(container: HTMLElement): string[] {
  return [...container.querySelectorAll('.ask-group__tab:not(.ask-group__tab--submit)')].map(
    (tab) => tab.textContent?.replace(/\s+/g, ' ').trim() ?? ''
  )
}

describe('AskGroupCard 的题签栏', () => {
  it('没给题签时，每道题的题号只出现一次', () => {
    setLocale('zh-CN')
    const { container } = renderCard([question('a'), question('b')])

    expect(tabTexts(container)).toEqual(['1', '2'])
  })

  it('给了题签就写题签，题号不再另起一枚', () => {
    setLocale('zh-CN')
    const { container } = renderCard([question('a', { header: '范围' }), question('b', { header: '口径' })])

    expect(tabTexts(container)).toEqual(['范围', '口径'])
  })

  it('答过的题前面打勾，题号留着', () => {
    setLocale('zh-CN')
    const answered = question('a', {
      answer_log: [{ kind: 'option', option: '甲', note: null, by: 'u1', at: '2026-08-01T00:01:00Z', reply_id: 'r1' }],
    })
    const { container } = renderCard([answered, question('b')])

    expect(tabTexts(container)).toEqual(['✓1', '2'])
  })
})
