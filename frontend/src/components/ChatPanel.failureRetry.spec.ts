import type { Component } from 'vue'
import type { Block, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

vi.mock('../lib/libraryApi', async () => ({
  ...(await vi.importActual<typeof import('../lib/libraryApi')>('../lib/libraryApi')),
  listProjectLibrary: vi.fn().mockResolvedValue({ data: [], next: null }),
}))
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false }),
  listBlocks: vi.fn(),
  listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  listTopicMembers: vi.fn().mockResolvedValue({
    data: [
      { member_handle: 'alice', name: 'Alice', role: 'owner', agent: false },
      { member_handle: 'cheese-room', name: '芝士', role: 'member', agent: true },
    ],
    total: 2,
  }),
  summonAgent: vi.fn(),
}))

import { listBlocks, summonAgent } from '@/api'
import ChatPanel from './ChatPanel.vue'

const failure = {
  id: 'failed-reply', conversation_id: 'retry-room', kind: 'event',
  author_type: 'platform', author: 'system', content: '本轮未完成', turn_id: 'failed-turn',
  created_at: '2026-10-12T09:16:00Z',
  meta: {
    event_type: 'turn_failed', severity: 'error', who: 'human', retryable: true,
    detail: 'API Error: Stream idle timeout - no chunks received (Unknown)', detail_label: '详情',
  },
} as unknown as Block

function topic(id = 'retry-room'): Topic {
  return {
    id, project_id: 'p1', parent_id: null, title: '重试', kind: 'topic', status: 'active',
    created_at: '2026-10-12T00:00:00Z',
  } as Topic
}

async function mount() {
  const view = render(ChatPanel as unknown as Component, {
    props: { topic: topic(), showComposer: true, hideHeader: true, noUpgrade: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await view.findByRole('button', { name: '重试' })
  return view
}

beforeEach(() => {
  vi.clearAllMocks()
  setLocale('zh-CN')
  vi.mocked(listBlocks).mockResolvedValue({ data: [failure], total: 1 })
  vi.stubGlobal('WebSocket', class {
    static OPEN = 1
    readyState = 1
    onopen = null
    onclose = null
    onerror = null
    onmessage = null
    close() {}
    send() {}
  })
})

describe('失败回复的重试入口', () => {
  it('点重试带上失败卡，开始后不再显示同一入口', async () => {
    vi.mocked(summonAgent).mockResolvedValue({ started: true })
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(summonAgent).toHaveBeenCalledWith('retry-room', 'failed-reply'))
    await waitFor(() => expect(view.queryByRole('button', { name: '重试' })).toBeNull())
  })

  it('没有候选时移除入口，但保留服务报错详情', async () => {
    vi.mocked(summonAgent).mockResolvedValue({ started: false, reason: 'nothing_pending' })
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(view.queryByRole('button', { name: '重试' })).toBeNull())
    await fireEvent.click(view.container.querySelector('details summary')!)
    expect(view.container.textContent).toContain(failure.meta!.detail)
  })

  it('网络请求失败时仍可重试同一张卡', async () => {
    vi.mocked(summonAgent).mockRejectedValueOnce(new Error('network unavailable'))
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    await view.findByText('network unavailable')
    expect(view.getByRole('button', { name: '重试' })).toBeTruthy()
    vi.mocked(summonAgent).mockResolvedValue({ started: true })
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(summonAgent).toHaveBeenCalledTimes(2))
  })

  it('切换对话后，旧重试的结果不让新对话显示正在回复', async () => {
    let resolve!: (result: { started: boolean }) => void
    vi.mocked(summonAgent).mockReturnValue(new Promise((done) => { resolve = done }))
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    vi.mocked(listBlocks).mockResolvedValue({ data: [], total: 0 })
    await view.rerender({ topic: topic('another-room') })
    resolve({ started: true })
    await waitFor(() => expect(view.queryByRole('button', { name: '重试' })).toBeNull())
    expect((view.emitted('working') ?? []).at(-1)).not.toEqual([true])
  })
})
