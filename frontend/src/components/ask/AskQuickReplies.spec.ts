// 卡上写了什么：算数的那一条在最上面，同一个人先说过的降一档列在下面。
//
// 2026-11-22 蔡松洋报的一件事：他在卡下面打字回了一句，卡上什么都没变，看着像没收到。
// 那次改完，打字就是作答，卡上必须写出来；他后来又点了一个选项，先前那句话也不能
// 在卡上消失。
import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AskQuickReplies from './AskQuickReplies.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

function question(answerLog: Record<string, unknown>[]): Block {
  return {
    id: 'q1',
    conversation_id: 'c1',
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: '这批题我按你说的发十几道，还是你自己来？',
    reply_to: null,
    refs: [],
    created_at: '2026-11-22T02:06:00Z',
    meta: {
      asked: ['me'],
      options: [{ text: '你先帮老师发十几题' }, { text: '我自己来' }],
      answer_log: answerLog,
    },
  } as unknown as Block
}

function renderCard(block: Block) {
  setLocale('zh-CN')
  return render(AskQuickReplies, {
    props: { block, names: { me: '蔡松洋' }, viewer: 'me' },
    global: { plugins: [vuetify, i18n] },
  })
}

const typed = {
  kind: 'note',
  option: null,
  note: '你先帮老师发个十几题',
  by: 'me',
  at: '2026-11-22T02:07:00Z',
  reply_id: 'r1',
}
const clicked = {
  kind: 'option',
  option: '我自己来',
  note: null,
  by: 'me',
  at: '2026-11-22T02:08:00Z',
  reply_id: 'r2',
}

describe('打字回复在卡上看得见', () => {
  it('打字回的那一句就是作答，卡上写着是谁说的什么，选项收起', () => {
    const { getByText, queryByText } = renderCard(question([typed]))

    expect(getByText('蔡松洋：你先帮老师发个十几题')).toBeTruthy()
    expect(queryByText('我自己来')).toBeNull()
  })

  it('老版本写下的 pending 照样画在卡上，不是空的', () => {
    const { getByText } = renderCard(question([{ ...typed, kind: 'pending' }]))

    expect(getByText('蔡松洋：你先帮老师发个十几题')).toBeTruthy()
  })

  it('先打字后点选：点选算数，先前那句降一档留在卡上', () => {
    const { getByText, container } = renderCard(question([typed, clicked]))

    expect(getByText('蔡松洋：我自己来')).toBeTruthy()
    expect(getByText('此前蔡松洋说过：你先帮老师发个十几题')).toBeTruthy()
    expect(container.querySelectorAll('.ask-replies__row--earlier')).toHaveLength(1)
  })
})
