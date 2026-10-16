// 触摸屏上回车是换行。
//
// 「Enter 发送 / Shift+Enter 换行」在有键盘的地方成立，软键盘上不成立——那儿没有
// Shift 这一层。照原来的规矩，手机用户打不出第二行：每次回车都把半句话发出去。
// 发送这一侧本来就有按钮，换行这一侧没有别的办法，所以让位的是发送。
//
// 判据是输入方式 `(hover: none)` 而不是视口宽度：带触摸屏的笔记本两样都对，
// 而窄窗口的桌面浏览器仍然有真键盘。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n from '@/i18n'
import { composerText, fillComposer } from '@/test/composerInput'

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

/** 让整台设备看起来是（或不是）触摸屏。组件在 setup 里问一次，所以要先于挂载。 */
function pretendTouchDevice(isTouch: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('hover: none') ? isTouch : false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  }))
}

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

function composerOf(isTouch: boolean): HTMLElement {
  pretendTouchDevice(isTouch)
  const { container } = render(Panel, {
    props: { topic, showComposer: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  const box = container.querySelector<HTMLElement>('.ProseMirror') as HTMLElement
  expect(box).toBeTruthy()
  return box
}

/** 打两个字、按一下回车之后，输入框里剩下的字：换了行就多一个换行，发出去了就空了。 */
async function afterEnter(box: HTMLElement, shiftKey = false): Promise<string> {
  box.focus()
  await fillComposer(box, '看看')
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey, bubbles: true, cancelable: true }))
  await Promise.resolve()
  return composerText(box)
}

describe('回车键在两种设备上的意思', () => {
  it('触摸屏：回车是换行', async () => {
    expect(await afterEnter(composerOf(true))).toBe('看看\n')
  })

  it('有指针设备：回车还是发送', async () => {
    expect(await afterEnter(composerOf(false))).toBe('')
  })

  it('触摸屏上 Shift+Enter 一样是换行', async () => {
    expect(await afterEnter(composerOf(true), true)).toBe('看看\n')
  })
})
