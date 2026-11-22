import type { Block } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AskGroupCard from './AskGroupCard.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

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

function renderCard(blocks: Block[]) {
  setLocale('zh-CN')
  return render(AskGroupCard, {
    props: { blocks, viewer: 'u1', names: {} },
    global: { plugins: [vuetify, i18n] },
  })
}

const answeredByOther = {
  kind: 'option',
  option: 'A',
  note: null,
  by: 'someone-else',
  at: '2026-08-01T00:01:00Z',
  reply_id: 'r1',
}

describe('整组卡：一道题被别人答了只锁那一道', () => {
  it('另一道题照样能选', async () => {
    // 第一题已经被别人答了，第二题还开着。
    const blocks = [question('a', { answer_log: [answeredByOther] }), question('b')]
    const { getAllByRole, queryByText } = renderCard(blocks)

    await fireEvent.click(getAllByRole('tab')[1]!)
    const radios = getAllByRole('radio')
    expect(radios.every((radio) => !(radio as HTMLInputElement).disabled)).toBe(true)
    expect(queryByText('这道题已有人回答，改不了')).toBeNull()
  })

  it('那道被答过的题自己锁住，并说明为什么', async () => {
    const blocks = [question('a', { answer_log: [answeredByOther] }), question('b')]
    const { getAllByRole, getByText } = renderCard(blocks)

    await fireEvent.click(getAllByRole('tab')[0]!)
    expect(getByText('这道题已有人回答，改不了')).toBeTruthy()
    expect(getAllByRole('radio').every((radio) => (radio as HTMLInputElement).disabled)).toBe(true)
  })
})
