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
  it('这一行不摆话题级的设置', () => {
    const actions = composer().querySelector('.composer-actions')!
    expect(actions.textContent?.replace(/\s/g, '')).toBe('')
  })
})
