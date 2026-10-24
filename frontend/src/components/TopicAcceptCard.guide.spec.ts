/** 导览的「采纳」那一站：卡上真有「退回 / 采纳」时才算到。
 *
 * 卡刚递来、合并态还在「状态更新中」的那一拍，`decisionOpen` 还是假，横条上只有状态
 * 词；导览要是这时把这一站算成「已经到了」，人照着浮窗去找那两颗按钮会发现没得点。
 * 手机上这一条只放「审阅」（决定在「改动」页底部），那也不算。
 */
import type { AcceptCard, MergeStateInfo } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

// 卡旁边还有这件任务的审阅意见；这里测的是卡，意见是空的。
vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
  }
})

import { __resetGuideAnchorsForTests, guideAnchor } from '@/composables/useGuideAnchors'

import TopicAcceptCard from './TopicAcceptCard.vue'

import i18n, { setLocale } from '@/i18n'

function mergeState(over: Partial<MergeStateInfo>): MergeStateInfo {
  return {
    state: 'unknown',
    who: 'platform',
    reasons: [{ kind: 'no_signal', checks: [], detail: '还没有信号' }],
    head_sha: null,
    checked_at: null,
    since: null,
    ...over,
  }
}

/** 绑了 GitHub 的仓库递来的一张卡，审阅人是 alice。 */
function card(state: MergeStateInfo, over: Partial<AcceptCard> = {}): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    focus: '最懂',
    change_subject: 'chore: do a thing',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: '',
    note_level: null,
    created_at: '2026-09-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    forge: {
      kind: 'github_app',
      reports_checks: true,
      hosts_proposals: true,
      can_write_remote: true,
      pushes_to_external_remote: true,
      identity: 'user',
      declaration: '',
    },
    pr_number: 888,
    pr_url: 'https://github.com/o/r/pull/888',
    pr_repo: 'o/r',
    pr_head_sha: '7091739',
    pr_merged_at: null,
    artifact: null,
    deliverable: null,
    merge_state: state,
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  }
}

const STILL_SETTLING = mergeState({
  // 递卡那一瞬间的常态：GitHub 还没算完能不能合。
  state: 'unknown',
  who: 'platform',
  reasons: [{ kind: 'github_verdict', checks: [], detail: 'GitHub 的裁决：unknown' }],
})
const DECIDABLE = mergeState({
  state: 'clean',
  who: 'human',
  reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
})

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mount(props: { reviewButton?: boolean; viewer?: string } = {}) {
  return render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active', taskId: 'k1', viewer: 'alice', ...props },
    global: { plugins: [i18n, createVuetify({ components, directives })] },
  })
}

const buttons = (container: Element) =>
  Array.from(container.querySelectorAll('.accept-bar button')).map((b) => b.textContent?.trim() ?? '')

beforeEach(() => {
  // 这几条断言读的是中文文案。
  setLocale('zh-CN')
  setActivePinia(createPinia())
  __resetGuideAnchorsForTests()
  getAcceptCards.mockReset()
})

describe('the guide’s accept stop', () => {
  it('has not arrived while the card is still settling and nothing can be decided', async () => {
    getAcceptCards.mockResolvedValue({ data: [card(STILL_SETTLING)], has_more: false })
    const { container } = mount()
    await flush()
    expect(guideAnchor('accept-card'), '卡还在收敛时不该登记这一站').toBeNull()
    expect(
      buttons(container).some((b) => b.includes('采纳')),
      '那时横条上也没有采纳'
    ).toBe(false)
  })

  it('arrives once 退回 and 采纳 are actually on the bar', async () => {
    getAcceptCards.mockResolvedValue({ data: [card(DECIDABLE)], has_more: false })
    const { container } = mount()
    await flush()
    const anchor = guideAnchor('accept-card')
    expect(anchor, '能决定时这一站该到了').not.toBeNull()
    // 圈住的那一块就该是画着那两颗按钮的横条：名字登记早一拍，人就点不到。
    expect(buttons(anchor as HTMLElement)).toEqual(
      expect.arrayContaining([expect.stringContaining('退回'), expect.stringContaining('采纳')])
    )
    expect(buttons(container).length).toBeGreaterThan(0)
  })

  it('stays away on the phone strip, which offers 审阅 instead of the two buttons', async () => {
    getAcceptCards.mockResolvedValue({ data: [card(DECIDABLE)], has_more: false })
    const { container } = mount({ reviewButton: true })
    await flush()
    expect(
      buttons(container).some((b) => b.includes('审阅')),
      '手机上这一条放的是审阅'
    ).toBe(true)
    expect(
      buttons(container).some((b) => b.includes('采纳')),
      '两颗决定按钮不在这一条上'
    ).toBe(false)
    expect(guideAnchor('accept-card'), '决定不在这儿，这一站也不该算到').toBeNull()
  })

  it('stays away when someone else is named as the reviewer', async () => {
    getAcceptCards.mockResolvedValue({ data: [card(DECIDABLE, { reviewer_handle: 'bob' })], has_more: false })
    mount()
    await flush()
    expect(guideAnchor('accept-card')).toBeNull()
  })
})
