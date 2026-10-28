/** 手机触屏长按消息打开的底部面板：能钉书签的地方给「加书签」，已经钉了的给「取消书签」。 */
import type { Block } from '../../cx_types'
import type { MenuAction } from '../common/menuAction'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../common/MobileActionSheet.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'MobileActionSheet',
      props: { modelValue: { type: Boolean, default: false }, actions: { type: Array, default: () => [] } },
      setup(props) {
        return () =>
          h(
            'div',
            (props.actions as MenuAction[]).map((a) =>
              h('button', { key: a.key, type: 'button', onClick: a.onSelect }, a.label)
            )
          )
      },
    }),
  }
})

import RoomMessageSheet from './RoomMessageSheet.vue'

import { setLocale } from '@/i18n'

const MESSAGE = {
  id: 'b1',
  conversation_id: 't1',
  kind: 'message',
  author_type: 'participant',
  author: 'alice',
  content: '先看登录',
  created_at: '2026-10-21T00:00:00Z',
} as unknown as Block

function mount(extra: Record<string, unknown>) {
  return render(RoomMessageSheet, {
    props: {
      modelValue: true,
      block: MESSAGE,
      isAgent: false,
      editable: false,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('手机长按消息面板里的书签', () => {
  it('能钉书签的地方给「加书签」', async () => {
    const ui = mount({ chapterable: true })
    await fireEvent.click(ui.getByRole('button', { name: '加书签' }))
    expect((ui.emitted().chapter as [Block][])[0]![0].id).toBe('b1')
  })

  it('已经钉过的消息给「取消书签」', async () => {
    const ui = mount({ chapterable: true, chapteredIds: new Set(['b1']) })
    await fireEvent.click(ui.getByRole('button', { name: '取消书签' }))
    expect((ui.emitted().unchapter as [Block][])[0]![0].id).toBe('b1')
  })

  it('不能钉书签的地方不出现这一项', () => {
    const ui = mount({ chapterable: false })
    expect(ui.queryByRole('button', { name: '加书签' })).toBeNull()
    expect(ui.queryByRole('button', { name: '取消书签' })).toBeNull()
  })
})
