import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
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
    content: `Question ${id}`,
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked: [],
      asked_all: true,
      notice_id: 'notice-1',
      options: [{ text: 'A' }, { text: 'B' }],
      answer_log: [],
      ...meta,
    },
  } as unknown as Block
}

const vuetify = createVuetify({ components, directives })

function renderCard(blocks: Block[]) {
  setLocale('zh-CN')
  return render(AskGroupCard, {
    props: { blocks, viewer: 'u1', names: {} },
    global: { plugins: [vuetify, i18n] },
  })
}

function tabTexts(tabs: HTMLElement[]): string[] {
  return tabs.map((tab) => tab.textContent?.replace(/\s+/g, ' ').trim() ?? '')
}

describe('AskGroupCard question numbers', () => {
  it.each([2, 3])('shows each question number once for %i questions without headers', (count) => {
    const { getAllByRole } = renderCard(Array.from({ length: count }, (_, i) => question(String(i))))

    expect(tabTexts(getAllByRole('tab'))).toEqual(Array.from({ length: count }, (_, i) => String(i + 1)))
  })

  it('preserves the existing number and header for labelled questions', () => {
    const { getAllByRole } = renderCard([question('a', { header: 'Scope' }), question('b', { header: 'Format' })])

    expect(tabTexts(getAllByRole('tab'))).toEqual(['1Scope', '2Format'])
  })

  it('keeps the number and completion mark when an answer is selected, and still switches questions', async () => {
    const { getAllByRole, getByRole, getByText } = renderCard([question('a'), question('b')])

    await fireEvent.change(getByRole('radio', { name: 'A' }))
    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '2'])

    await fireEvent.click(getByRole('tab', { name: '2' }))
    expect(getByText('Question b')).toBeTruthy()
  })

  it('keeps the number and completion mark for recorded answers', () => {
    const answered = question('a', {
      answer_log: [{ kind: 'option', option: 'A', note: null, by: 'u1', at: '2026-08-01T00:01:00Z', reply_id: 'r1' }],
    })
    const { getAllByRole } = renderCard([answered, question('b')])

    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '2'])
  })
})
