// 输入区是两行：输入框独占一整行，动作在下面那行——左边动作、右边发送。
//
// 原来是一行并排：输入框 + 图片 + 发送。按钮只会越加越多，而每加一颗，真正能打字
// 的那块就窄一点；在 390px 的手机上实测只剩 144px，不到屏宽的四成。所以让位的是
// 并排这件事本身，不是某一颗按钮。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

beforeEach(() => {
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : { code: 200, data: { data: [], total: 0 } },
  }))
})

function composer() {
  const { container } = render(Panel, {
    props: { topic, showComposer: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return container
}

// 默认语言是 en（happy-dom 的 navigator.language 是 en-US），而这份用例断言的是
// 中文界面的字。先把语言钉住，别让它跟着环境飘。
beforeEach(() => setLocale('zh-CN'))
describe('输入区的两行', () => {
  it('输入框不和任何按钮共用一行', () => {
    const c = composer()
    const actions = c.querySelector('.composer-actions')
    expect(actions).toBeTruthy()
    expect(actions!.querySelector('textarea')).toBeNull()
  })

  it('动作和发送都在下面那行，发送在最后', () => {
    const c = composer()
    const actions = c.querySelector('.composer-actions')!
    const buttons = Array.from(actions.querySelectorAll('button'))
    expect(buttons.length).toBeGreaterThan(1)
    // 这一行的方向是"左动作、右发送"。
    expect(buttons[0].className).toContain('composer-icon')
    expect(buttons[buttons.length - 1].className).toContain('composer-send')
  })

  // 这一行只放**这条消息**的动作。话题级的设置（谁在跑、在哪跑）不在这儿——
  // 它们发第一条消息之后就不再变，摆在这里只是占着 390px 里最贵的一行。
  //
  // 「交给芝士」是这一行唯一带字的一颗，而它恰恰是「这条消息」的动作：它决定
  // 这条消息叫不叫它。带字是故意的——一个光秃秃的 @ 图标猜不出来，而「怎么叫
  // 它」正是这个产品里最该一眼看见的事。窄屏上那三个字由 CSS 收起来。
  // 名册还没到（或这个房间根本没有芝士的座位）时，浏览器解析不出它的 handle，
  // 替人写进正文的那个 @ 就只是一行字：消息照发、它照样不动。所以这一瞬间这颗
  // 按钮是关着的——少一个入口，好过一个点了不算数的入口。
  it('还不知道芝士是谁的时候，「交给芝士」是关着的', () => {
    const btn = composer().querySelector<HTMLButtonElement>('.summon-btn')!
    expect(btn.disabled).toBe(true)
  })

  it('这一行只放这条消息自己的动作', () => {
    const actions = composer().querySelector('.composer-actions')!
    expect(actions.textContent?.replace(/\s/g, '')).toBe('交给芝士')
  })
})
