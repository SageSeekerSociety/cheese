/** 他自己的芝士替他发的消息，署名照旧是他，名字旁边说是芝士代发的；他自己发的不说。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import RoomMessage from './RoomMessage.vue'

import i18n, { setLocale, t } from '@/i18n'

function mount(meta: Record<string, unknown> | null) {
  return render(RoomMessage, {
    props: {
      block: { id: 'b1', author: 'alice', content: '周五组会改到下午三点', kind: 'message', created_at: '', meta },
      parent: null,
      parentName: null,
      runStart: true,
      mine: false,
      topicId: 't1',
      authorName: 'Alice',
      avatar: null,
      isAgent: false,
      time: '10:00',
      refs: { mentionNames: {}, topicTitles: {} },
      viewer: 'bob',
      pickerOpen: false,
      askBusy: false,
    } as never,
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('芝士代发', () => {
  it('a message her 芝士 wrote says so beside her name, and whose 芝士 it was', () => {
    const view = mount({ by_assistant: true })

    const tag = view.getByText(t('work.onBehalf.tag'))
    expect(tag.getAttribute('title')).toBe(t('work.onBehalf.tagHint', { name: 'Alice' }))
    expect(view.getByText('Alice')).toBeTruthy()
  })

  it('a message she wrote herself says nothing of the kind', () => {
    const view = mount(null)

    expect(view.queryByText(t('work.onBehalf.tag'))).toBeNull()
  })
})
