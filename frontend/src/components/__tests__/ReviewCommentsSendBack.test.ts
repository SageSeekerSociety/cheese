/** 退回时带上批注：在「改动」里写下、还没送出的批注列在退回那一块里，默认一起送出；
 *  移掉的那条这一次不送。横条上说还有几条没送出。 */
import type { AcceptCard } from '../../cx_types'
import type { ReviewComment } from '../../types/reviewComment'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const rejectCard = vi.fn()
const listReviewComments = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    rejectCard: (...a: unknown[]) => rejectCard(...a),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})
vi.mock('../../api/reviewComments', () => ({
  listReviewComments: (...a: unknown[]) => listReviewComments(...a),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => null }))

import { AcceptPage } from './acceptHarness'

import i18n, { setLocale } from '@/i18n'

const PENDING = {
  id: 'card-1',
  topic_id: 't1',
  task_id: 'task-1',
  reviewer_handle: 'alice',
  focus: '',
  change_subject: 'chore: do a thing',
  change_body: null,
  status: 'pending',
  decided_by: null,
  decided_at: null,
  note: '',
  note_level: null,
  created_at: '2026-08-01T00:00:00Z',
  gate_passed_at: null,
  gate_output: '',
  approvals: [],
  approvals_required: 1,
  pr_number: null,
  pr_url: null,
  forge: {
    kind: 'forgejo',
    reports_checks: false,
    hosts_proposals: false,
    can_write_remote: false,
    pushes_to_external_remote: false,
    identity: 'platform',
    declaration: '',
  },
  merge_state: { state: 'unknown', who: 'human', reasons: [], head_sha: null, checked_at: null, since: null },
  auto_merge: { allowed: false, armed_by: null, armed_at: null },
} as unknown as AcceptCard

function draft(id: string, body: string, line: number): ReviewComment {
  return {
    id,
    author: 'alice',
    path: 'src/app.ts',
    line_start: line,
    line_end: line,
    line_text: 'x',
    place: `L${line}`,
    current_line: line,
    body,
    suggestion: null,
    parent_id: null,
    state: 'draft',
    card_id: null,
    sent_at: null,
    outcome: null,
    outcome_note: null,
    created_at: '2026-08-01T00:00:00Z',
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

const buttonNamed = (container: Element, label: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)

beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset().mockResolvedValue({ data: [PENDING], has_more: false })
  rejectCard.mockReset().mockResolvedValue({ ...PENDING, status: 'rejected' })
  listReviewComments.mockReset().mockResolvedValue({
    comments: [draft('d-1', '归档的也要排除', 208), draft('d-2', '这条以后再说', 30)],
  })
})

async function mount() {
  const utils = render(AcceptPage, {
    props: { topicId: 't1', topicStatus: 'active', taskId: 'task-1' },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await flush()
  return utils
}

describe('退回时带上批注', () => {
  it('横条上说还有几条没送出', async () => {
    const { container } = await mount()
    expect(container.querySelector('.accept-bar')!.textContent).toContain('2 条批注未发送')
  })

  it('没送出的批注默认一起送出；移掉的那条这次不送', async () => {
    const { container } = await mount()
    await fireEvent.click(buttonNamed(container.querySelector('.accept-bar')!, '退回')!)
    const form = container.querySelector('.reject-form')!
    expect(form.textContent).toContain('归档的也要排除')
    expect(form.textContent).toContain('这条以后再说')

    const later = Array.from(form.querySelectorAll('.reject-form__item')).find((li) =>
      li.textContent?.includes('这条以后再说')
    )!
    await fireEvent.click(later.querySelector('button[aria-label="这次不送"]')!)
    await fireEvent.click(buttonNamed(form, '退回')!)
    await flush()

    expect(rejectCard).toHaveBeenCalledWith('card-1', 'alice', '', ['d-1'])
  })

  it('不写理由，只带批注也能退回', async () => {
    const { container } = await mount()
    await fireEvent.click(buttonNamed(container.querySelector('.accept-bar')!, '退回')!)
    await fireEvent.click(buttonNamed(container.querySelector('.reject-form')!, '退回')!)
    await flush()
    expect(rejectCard).toHaveBeenCalledWith('card-1', 'alice', '', ['d-1', 'd-2'])
  })
})
