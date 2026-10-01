// 在房间里问一道带选项的题：输入框旁边那颗按钮打开提问框，发成了框关上、那道题
// 出现在对话里；没发成框不关，原因写在框里，写下的字都在。和芝士私聊时没有这颗
// 按钮：那里没有别人来答。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const askRoom = vi.fn()
vi.mock('../api/optionQuestions', () => ({
  askRoom: (...a: unknown[]) => askRoom(...a),
  answerOptions: vi.fn(),
}))

import ChatPanel from './ChatPanel.vue'

import i18n, { setLocale } from '@/i18n'

const Panel = ChatPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'alice',
  created_at: '2026-09-08T00:00:00Z',
  updated_at: '2026-09-08T00:00:00Z',
} as Topic

beforeAll(() => {
  // Vuetify's dialog positions itself against these; happy-dom has neither.
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'alice' }))
  askRoom.mockReset()
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async () => ({
    ok: true,
    status: 200,
    json: async () => ({ code: 200, data: { data: [], total: 0 } }),
  }))
})

function mount(alwaysSummon = false) {
  return render(Panel, {
    props: { topic, showComposer: true, alwaysSummon },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

async function fillAndSend() {
  await fireEvent.click(screen.getByRole('button', { name: '带选项提问' }))
  await fireEvent.update(await screen.findByLabelText('问题'), '周会挪到周四吗')
  await fireEvent.update(screen.getByLabelText('选项 1'), '行')
  await fireEvent.update(screen.getByLabelText('选项 2'), '不行')
  // The composer has its own 发送; this one is the dialog's.
  const dialog = screen.getByRole('dialog')
  await fireEvent.click(within(dialog).getByRole('button', { name: '发送' }))
}

describe('在房间里问一道带选项的题', () => {
  it('发成了框关上，那道题出现在对话里', async () => {
    askRoom.mockResolvedValue({
      id: 'q1',
      author: 'alice',
      content: '周会挪到周四吗',
      kind: 'message',
      created_at: '2026-09-08T00:00:01Z',
      meta: { options: ['行', '不行'], asked: null },
    })
    mount()
    await fillAndSend()

    expect(askRoom).toHaveBeenCalledWith('t1', '周会挪到周四吗', ['行', '不行'])
    // A closed dialog keeps its DOM until the leave transition ends, which
    // happy-dom never runs; the overlay stops being active the moment it closes.
    await waitFor(() =>
      expect(document.querySelector('.v-overlay--active .v-dialog, .v-dialog.v-overlay--active')).toBeNull()
    )
    expect(await screen.findByText('周会挪到周四吗')).toBeTruthy()
  })

  it('没发成框不关，原因写在框里，字都在', async () => {
    askRoom.mockRejectedValue(new Error('只有房间成员能在这里提问'))
    mount()
    await fillAndSend()

    expect(await screen.findByText('只有房间成员能在这里提问')).toBeTruthy()
    expect((screen.getByLabelText('问题') as HTMLInputElement).value).toBe('周会挪到周四吗')
  })

  it('和芝士私聊时没有这颗按钮', () => {
    mount(true)
    expect(screen.queryByRole('button', { name: '带选项提问' })).toBeNull()
  })
})
