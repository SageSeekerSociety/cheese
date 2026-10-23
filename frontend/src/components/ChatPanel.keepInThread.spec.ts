// 在支线里把一份文件存进资料库，请求发给支线，不发给频道：后端按这个 id 决定「已存
// 进资料库」那句话说在哪儿。发给频道，那句话就落到主线上，和支线里的事对不上。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale, t } from '@/i18n'

const mocks = vi.hoisted(() => ({ save: vi.fn().mockResolvedValue({ name: 'gemm_bench.cu' }) }))
vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
    listBlocks: vi.fn().mockResolvedValue({
      data: [
        {
          id: 'b1',
          conversation_id: 'thread-1',
          kind: 'artifact',
          author_type: 'agent',
          author: 'cheese-room',
          content: 'gemm_bench.cu',
          mime_type: 'text/plain',
          meta: {},
          created_at: '2026-10-23T10:00:00Z',
        },
      ],
      total: 1,
    }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listTopicMembers: vi.fn().mockResolvedValue({
      data: [{ member_handle: 'alice', name: 'Alice', role: 'owner', agent: false }],
      total: 1,
    }),
    saveRoomOutputToLibrary: mocks.save,
  }
})

import ChatPanel from './ChatPanel.vue'

const Panel = ChatPanel as unknown as Component

function channel(): Topic {
  return {
    id: 'channel-1',
    project_id: 'p1',
    parent_id: null,
    title: '综合',
    kind: 'topic',
    status: 'active',
    created_at: '2026-10-07T00:00:00Z',
  } as Topic
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeAll(() => {
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  // 文件的 ⋯ 是一只 VMenu（VOverlay），happy-dom 没有 visualViewport，不补上它挂不起来。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      onmessage: (() => void) | null = null
      close() {}
      send() {}
    }
  )
})

describe('支线里存进资料库', () => {
  it('请求发给支线，不发给频道', async () => {
    const view = render(Panel, {
      props: { topic: channel(), conversationId: 'thread-1', hideHeader: true },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })
    await flush()

    await fireEvent.click(await view.findByTestId('file-menu'))
    await fireEvent.click(await view.findByText(t('work.room.file.keep')))
    await flush()

    expect(mocks.save).toHaveBeenCalledWith('thread-1', 'gemm_bench.cu')
  })
})
