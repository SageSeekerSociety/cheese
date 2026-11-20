/** 主线消息下的快捷回答：只给它问的那个人；点一个就是他回这条消息说的那句话；有人答过
 *  之后不再给。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale } from '@/i18n'

function mount(meta: Record<string, unknown>, viewer = 'alice') {
  return render(RoomMessage, {
    props: {
      block: {
        id: 'q1',
        kind: 'message',
        author: 'cheese-t1',
        created_at: '',
        content: '报名的人要填什么？我建议照去年四项。',
        meta: {
          quick_replies: true,
          options: [{ text: '照去年四项' }, { text: '只要姓名和学号' }],
          asked: ['alice'],
          asked_all: false,
          answer_log: [],
          ...meta,
        },
      },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      authorName: '芝士',
      avatar: null,
      isAgent: true,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer,
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('主线消息下的快捷回答', () => {
  it('消息照常显示，点一个回答就是以自己的名义回这条消息', async () => {
    const view = mount({})
    expect(screen.getByText('报名的人要填什么？我建议照去年四项。')).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: '只要姓名和学号' }))
    expect(view.emitted()['ask-reply']).toEqual([[expect.objectContaining({ id: 'q1' }), '只要姓名和学号']])
  })

  it('问的不是看的人时，看不到回答按钮', () => {
    mount({}, 'bob')
    expect(screen.queryByRole('button', { name: '照去年四项' })).toBeNull()
  })

  it('有人答过之后不再给回答按钮', () => {
    mount({ answer_log: [{ kind: 'option', option: '照去年四项', note: null, by: 'alice', at: '', reply_id: 'r1' }] })
    expect(screen.queryByRole('button', { name: '照去年四项' })).toBeNull()
    expect(screen.queryByRole('button', { name: '只要姓名和学号' })).toBeNull()
  })
})
