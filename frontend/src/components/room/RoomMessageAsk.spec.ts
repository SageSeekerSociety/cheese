/** 带选项的问题在时间线上：别人点一个选项就是回答，问的人不答自己的题。 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import i18n, { setLocale } from '../../i18n'

import RoomMessage from './RoomMessage.vue'

const question = {
  id: 'q1',
  author: 'alice',
  content: '周会挪到周四行吗？',
  kind: 'message',
  created_at: '',
  meta: { options: ['行', '不行'], asked: null },
}

function show(viewer: string) {
  return render(RoomMessage as unknown as Component, {
    props: {
      block: question,
      parent: null,
      parentName: null,
      runStart: true,
      mine: viewer === 'alice',
      topicId: 't1',
      authorName: 'Alice',
      avatar: null,
      isAgent: false,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer,
      askBusy: false,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

function option(out: ReturnType<typeof show>, text: string): HTMLButtonElement {
  const found = Array.from(out.container.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  if (!found) throw new Error(`no option ${text}`)
  return found as HTMLButtonElement
}

beforeEach(() => setLocale('zh-CN'))

describe('带选项的问题', () => {
  it('房间里的别人点一个选项，就是回答这道题', async () => {
    const out = show('bob')
    await fireEvent.click(option(out, '行'))
    expect(out.emitted('answer')).toEqual([[question, '行']])
  })

  it('问的人看得见自己的选项，但点不了', async () => {
    const out = show('alice')
    expect(option(out, '行').disabled).toBe(true)
    expect(option(out, '不行').disabled).toBe(true)
    await fireEvent.click(option(out, '行'))
    expect(out.emitted('answer')).toBeUndefined()
  })
})
