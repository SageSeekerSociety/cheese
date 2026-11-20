/** 频道里审阅一件交付的那一格：页头上决定；决定过的那一版页头写结果、改动只读——和任务页上一样。
 *
 * 这一格以前在采纳之后什么标记都没有：底部那一条只在卡等人时出现，页头又不读卡的结局，人回到
 * 这一格看到的还是一版像在等审阅的改动。
 */
import type { Component } from 'vue'
import type { AcceptCard, MergeStateInfo, Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
// Whether accepting unlocks an invite is the invite prompt's business, not this pane's.
vi.mock('@/composables/useInvitePrompt', () => ({
  primeInvitePrompt: vi.fn(async () => {}),
  noteAccepted: vi.fn(async () => {}),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getHandInStanding: vi.fn().mockResolvedValue({ unhanded: false, waiting: false }),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    getPreview: vi.fn(async () => null),
    getTopicWorkSummary: vi.fn(async () => ({ has_run: true, changed_files: ['form.vue'] })),
    listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
  }
})

import WorkPanel from './WorkPanel.vue'

import i18n, { setLocale } from '@/i18n'

// The 改动 tab's own suites cover what it draws: here it only says whether it is read-only.
const ChangesProbe = defineComponent({
  props: { readOnly: Boolean },
  setup: (props) => () => h('div', { 'data-testid': 'changes', 'data-read-only': String(props.readOnly) }),
})

const DECIDABLE: MergeStateInfo = {
  state: 'clean',
  who: 'human',
  reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
  head_sha: null,
  checked_at: null,
  since: null,
}

function card(over: Partial<AcceptCard>): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 'c1',
    reviewer_handle: 'alice',
    focus: '',
    change_subject: 'feat: shorter form',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: '',
    note_level: null,
    created_at: '2026-11-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    forge: {
      kind: 'forgejo',
      reports_checks: false,
      hosts_proposals: true,
      can_write_remote: true,
      pushes_to_external_remote: false,
      identity: 'platform',
      declaration: '',
    },
    pr_number: 12,
    pr_url: 'https://forge.example/o/r/pulls/12',
    pr_repo: null,
    pr_head_sha: null,
    pr_merged_at: null,
    artifact: null,
    deliverable: null,
    merge_state: DECIDABLE,
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

const topic = { id: 'c1', project_id: 'p1', kind: 'topic', title: '设计', status: 'active' } as unknown as Topic

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

// `tab`：主线上「查看改动」打开这件任务时点名的那一种看法；不点名（点任务标签）时不给。
function mount(tab: string | null = 'changes', askReturn: string | null = null) {
  return render(WorkPanel as unknown as Component, {
    props: {
      topic,
      taskId: 'k1',
      review: true,
      reviewHead: { heading: '报名表改短', version: 2 },
      activityTick: 0,
      tab: tab ?? undefined,
      askReturn,
    },
    global: {
      plugins: [i18n, createVuetify({ components, directives })],
      stubs: { PanelChangesHost: ChangesProbe, PanelPreviewHost: true },
    },
    slots: { chat: '<div data-testid="task-chat">任务的对话</div>' },
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'alice', nickname: 'alice' }))
  getAcceptCards.mockReset()
})

describe('频道里打开的一件任务', () => {
  it('点任务标签打开时先看这件任务的对话，改动和预览在旁边两格', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { getByTestId, getByRole, queryByTestId } = mount(null)
    await flush()
    expect(getByTestId('task-chat').closest('[style*="display: none"]')).toBeNull()
    expect(queryByTestId('changes')).toBeNull()
    getByRole('tab', { name: /改动/ }).click()
    await flush()
    expect(getByTestId('changes')).toBeTruthy()
  })

  it('主线上点「退回」打开时，写退回理由的那一块已经开着，只开一次', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { findByPlaceholderText, emitted } = mount('changes', 'k1:1')
    expect(await findByPlaceholderText('退回理由（可选）')).toBeTruthy()
    expect(emitted()['return-asked']).toHaveLength(1)
  })

  it('没按「退回」打开时不开写理由的那一块', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { queryByPlaceholderText } = mount('changes')
    await flush()
    expect(queryByPlaceholderText('退回理由（可选）')).toBeNull()
  })

  it('主线上点「查看改动」打开时直接是改动', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { getByTestId } = mount('changes')
    await flush()
    expect(getByTestId('changes').closest('[style*="display: none"]')).toBeNull()
    expect(getByTestId('task-chat').closest('[style*="display: none"]')).not.toBeNull()
  })
})

describe('频道里审阅的那一版', () => {
  it('等我审阅时，页头写第几版，给退回和采纳，改动能写批注', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { getByText, getByRole, queryByTestId, getByTestId } = mount()
    await flush()
    expect(getByText('第 2 版')).toBeTruthy()
    expect(getByRole('button', { name: /退回/ })).toBeTruthy()
    expect(getByRole('button', { name: /^采纳/ })).toBeTruthy()
    expect(queryByTestId('delivery-decided')).toBeNull()
    expect(getByTestId('changes').dataset.readOnly).toBe('false')
  })

  it('采纳过的那一版：页头写「已采纳」，没有按钮，改动只读', async () => {
    getAcceptCards.mockResolvedValue({
      data: [card({ status: 'accepted', decided_by: 'alice', decided_at: '2026-11-02T00:00:00Z' })],
      has_more: false,
    })
    const { getByTestId, queryByRole } = mount()
    await flush()
    expect(getByTestId('delivery-decided').textContent).toContain('已采纳')
    expect(queryByRole('button', { name: /退回/ })).toBeNull()
    expect(getByTestId('changes').dataset.readOnly).toBe('true')
  })

  it('退回过的那一版：页头写「已退回」，改动只读', async () => {
    getAcceptCards.mockResolvedValue({
      data: [card({ status: 'rejected', decided_by: 'alice', decided_at: '2026-11-02T00:00:00Z' })],
      has_more: false,
    })
    const { getByTestId } = mount()
    await flush()
    expect(getByTestId('delivery-decided').textContent).toContain('已退回')
    expect(getByTestId('changes').dataset.readOnly).toBe('true')
  })
})
