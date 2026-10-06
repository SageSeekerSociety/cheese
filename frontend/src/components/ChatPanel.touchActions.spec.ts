// 触屏上一条消息的操作：长按这一条，从底部升起它的操作面板；轻点不算。触屏上没有
// 悬停，悬停条不该出现（浏览器补发的 mouseover 会把它叫出来，然后再也收不回去）。
import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale, t } from '@/i18n'

const Panel = ChatPanel as unknown as Component
let vuetify: ReturnType<typeof createVuetify>
let history: Block[] = []

function msg(id: string, content: string): Block {
  return {
    id,
    project_id: 'p1',
    conversation_id: 't1',
    kind: 'message',
    author_type: 'participant',
    author: 'other',
    content,
    reply_to: null,
    refs: [],
    created_at: new Date().toISOString(),
  } as unknown as Block
}

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

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  // 面板是 VOverlay，happy-dom 没有 visualViewport，不补上浮层挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 390,
    height: 844,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes('/progress')
        ? { code: 200, data: { items: [], updated_at: null } }
        : String(url).includes('/tasks')
          ? { code: 200, data: { data: [], total: 0 } }
          : { code: 200, data: { data: history, total: history.length, has_more: false } },
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

const settle = () => new Promise((r) => setTimeout(r, 0))

async function mountRoom(blocks: Block[]) {
  history = blocks
  const view = render(Panel, {
    props: { topic: { id: 't1', project_id: 'p1', title: 't1', kind: 'topic' } as Topic, showComposer: true },
    global: { plugins: [vuetify, i18n] },
  })
  await settle()
  await settle()
  return view
}

function touch(el: Element, type: string) {
  el.dispatchEvent(
    new PointerEvent(type, {
      bubbles: true,
      cancelable: true,
      pointerId: 1,
      pointerType: 'touch',
      clientX: 20,
      clientY: 20,
    })
  )
}

async function press(el: Element, ms: number) {
  touch(el, 'pointerdown')
  await new Promise((r) => setTimeout(r, ms))
  touch(el, 'pointerup')
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  await settle()
}

const sheetItems = () => screen.queryAllByRole('menuitem')

describe('触屏上消息的操作', () => {
  it('长按一条消息，打开它的操作面板；选回复，回复的就是这一条', async () => {
    pretendTouchDevice(true)
    const { container } = await mountRoom([msg('m1', '第一条'), msg('m2', '第二条')])

    await press(container.querySelector('[data-mid="m2"] .im-text')!, 550)
    await waitFor(() => expect(sheetItems().length).toBeGreaterThan(0))

    await fireEvent.click(screen.getByRole('menuitem', { name: t('work.room.message.reply') }))
    await waitFor(() => expect(container.querySelector('.reply-chip')?.textContent).toContain('第二条'))
  })

  it('长按一条消息，选「选择文字」，这一条单独放到一页上', async () => {
    pretendTouchDevice(true)
    const { container } = await mountRoom([msg('m1', '第一条'), msg('m2', '第二条里要挑一段出来')])

    await press(container.querySelector('[data-mid="m2"] .im-text')!, 550)
    await waitFor(() => expect(sheetItems().length).toBeGreaterThan(0))

    await fireEvent.click(screen.getByRole('menuitem', { name: t('work.room.message.selectText') }))
    // 操作面板收起的那一下它也还是个 dialog，但面板上没有消息的字。
    const page = () => screen.queryAllByRole('dialog').find((d) => d.textContent?.includes('第二条里要挑一段出来'))
    await waitFor(() => expect(page()).toBeTruthy())
    expect(page()?.textContent).not.toContain('第一条')
  })

  it('轻点一下不打开面板', async () => {
    pretendTouchDevice(true)
    const { container } = await mountRoom([msg('m1', '第一条')])

    await press(container.querySelector('[data-mid="m1"] .im-text')!, 80)
    await new Promise((r) => setTimeout(r, 500))
    expect(sheetItems()).toHaveLength(0)
  })

  it('触屏上划过消息不叫出悬停条', async () => {
    pretendTouchDevice(true)
    const { container } = await mountRoom([msg('m1', '第一条')])
    await fireEvent.mouseOver(container.querySelector('[data-mid="m1"] .im-text')!)
    expect(container.querySelector('.hover-bar')?.getAttribute('aria-hidden')).not.toBe('false')
  })
})
