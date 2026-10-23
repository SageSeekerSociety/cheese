/** 消息的 ⋯ 里的书签：能钉的地方给「加书签」，已经钉了的给「取消书签」。 */
import type { MenuAction } from '@/components/common/menuAction'
import type { Block } from '../../cx_types'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 菜单的弹层在测试环境里画不出来；这里只关心菜单里给了哪几项、点了做什么。
// 菜单项本身要在菜单「打开」之后才算出来，所以这个替身一挂上就把它打开。
vi.mock('../common/AdaptiveMenu.vue', async () => {
  const { defineComponent, h, onMounted } = await import('vue')
  return {
    default: defineComponent({
      name: 'AdaptiveMenu',
      props: { modelValue: { type: Boolean, default: false }, actions: { type: Array, default: () => [] } },
      emits: ['update:modelValue'],
      setup(props, { emit }) {
        onMounted(() => emit('update:modelValue', true))
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

import RoomHoverBar from './RoomHoverBar.vue'

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

async function mount(extra: Record<string, unknown>) {
  const ui = render(RoomHoverBar, {
    props: {
      block: MESSAGE,
      shown: true,
      top: 0,
      jump: false,
      isAgent: false,
      pickerOpen: false,
      editable: false,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  // 替身把菜单打开，菜单项要等这一下更新。
  await nextTick()
  return ui
}

beforeEach(() => setLocale('zh-CN'))

function bookmarkButton(container: Element): HTMLButtonElement | null {
  return container.querySelector<HTMLButtonElement>('button[title="加书签"], button[title="取消书签"]')
}

describe('消息悬停条上的书签按钮', () => {
  it('在悬停条上直接就能加书签', async () => {
    const ui = await mount({ chapterable: true })
    const button = bookmarkButton(ui.container)
    expect(button, '能加书签的地方悬停条上没有书签按钮').toBeTruthy()

    await fireEvent.click(button!)
    expect((ui.emitted().chapter as [Block][])[0]![0].id).toBe('b1')
  })

  it('加过的书签是实心，再点取消', async () => {
    const ui = await mount({ chapterable: true, chapteredIds: new Set(['b1']) })
    const button = bookmarkButton(ui.container)
    expect(button?.title).toBe('取消书签')
    expect(button?.querySelector('.mdi-bookmark')).toBeTruthy()

    await fireEvent.click(button!)
    expect((ui.emitted().unchapter as [Block][])[0]![0].id).toBe('b1')
  })

  it('没有书签的地方不画这颗按钮', async () => {
    const ui = await mount({ chapterable: false })
    expect(bookmarkButton(ui.container)).toBeNull()
  })
})
