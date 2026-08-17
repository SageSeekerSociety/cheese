// 点「@芝士」之后，焦点必须回到输入框。
//
// 不回的话 chip 自己一直握着焦点，用户接下来按的那次 Enter 打在 chip 上：把刚
// 点亮的 @芝士 又关掉，而且那次 Enter 也不发送。「先打字、再点 @芝士、再按
// Enter」是很自然的顺序，走这条路的人得到的是——消息正常发出去了、芝士不来、
// 页面上没有任何东西说明为什么。和「忘了 @」完全一样，所以没人查得出来。
//
// 2026-08-13 在 dev 上复现确认：点亮 → 按 Enter → class 从 summon-chip--on 掉回
// summon-chip；那条消息的 turn 记录里 summon=false，0.1 秒空转结束、零输出。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

const topic: Topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-13T00:00:00Z',
  updated_at: '2026-08-13T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // 组件挂上就会连 WS 取历史消息；这个测试只关心焦点，别让它真去连。
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  // URL 敏感：进度接口和消息列表的载荷形状不一样，一份糊弄两边会让面板
  // 在渲染时炸掉（第一版就是这么写的，测试自己通过、渲染在后台抛）。
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : { code: 200, data: { data: [], total: 0 } },
  }))
})

describe('@芝士 开关不吃掉焦点', () => {
  it('点一下之后，焦点在输入框上，而不是留在 chip 上', async () => {
    const { container } = render(Panel, {
      props: { topic, showComposer: true },
      global: { plugins: [vuetify] },
    })

    const chip = container.querySelector('.summon-chip') as HTMLButtonElement
    expect(chip).toBeTruthy()

    chip.focus()
    await fireEvent.click(chip)
    await new Promise((r) => setTimeout(r, 0))

    const textarea = container.querySelector('textarea')
    expect(textarea).toBeTruthy()
    // 这一行就是整个 bug：焦点若还在 chip 上，下一次 Enter 就把开关关了。
    expect(document.activeElement).toBe(textarea)
  })

  it('点一下确实把开关打开了（别为了修焦点把功能修没）', async () => {
    const { container } = render(Panel, {
      props: { topic, showComposer: true },
      global: { plugins: [vuetify] },
    })

    const chip = container.querySelector('.summon-chip') as HTMLButtonElement
    expect(chip.className).not.toContain('summon-chip--on')

    await fireEvent.click(chip)

    expect(chip.className).toContain('summon-chip--on')
  })
})
