/** 待采纳的卡是快照，而它描述的东西还在变。
 *
 * 采纳按钮亮不亮，看的是卡上那份 `merge_state`。递卡的一瞬间 PR 刚从草稿翻成
 * 待看，GitHub 还没算完能不能合，后端如实给 `unknown`——不可采纳，且注释写明
 * 「下一轮读到真值自然收敛」。所以界面必须真的去读第二次：不读，那颗按钮就一直
 * 灰着，验收人只能靠刷新页面才点得动（#888 上真实发生过）。
 */
import type { AcceptCard, MergeStateInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import TopicAcceptCard from '../TopicAcceptCard.vue'

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

/** 一张绑了 GitHub 的待采纳卡——只有这种卡的采纳按钮才会按合并态置灰。 */
function pendingCard(state: MergeStateInfo): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    routing_reason: '最懂',
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
    has_external_checks: true,
    pr_number: 888,
    pr_url: 'https://github.com/o/r/pull/888',
    pr_repo: 'o/r',
    pr_head_sha: '7091739',
    pr_merged_at: null,
    merge_state: state,
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
  }
}

const JUST_LANDED = mergeState({
  // GitHub 还没算完能不能合——递卡那一瞬间的常态。
  state: 'unknown',
  reasons: [{ kind: 'github_verdict', checks: [], detail: 'GitHub 的裁决：unknown' }],
})
const SETTLED = mergeState({
  state: 'clean',
  who: 'human',
  reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
})

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function acceptButton(container: Element): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('采纳'))
  expect(btn, '采纳按钮应该在卡上').toBeTruthy()
  return btn as HTMLButtonElement
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => vi.useRealTimers())

describe('待采纳的卡会自己跟上后端', () => {
  it('后端收敛成可合并之后，采纳按钮不用刷新页面就亮', async () => {
    getAcceptCards.mockResolvedValue({ data: [pendingCard(JUST_LANDED)], has_more: false })
    const { container } = render(TopicAcceptCard, {
      props: { topicId: 't1', topicStatus: 'active' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await flush()
    expect(acceptButton(container).disabled, '刚递到手的卡还不能采纳').toBe(true)

    // 后端这边收敛了（实测 #888 是一分钟内的事），但没有人碰过界面。
    getAcceptCards.mockResolvedValue({ data: [pendingCard(SETTLED)], has_more: false })
    await vi.advanceTimersByTimeAsync(15_000)
    await flush()

    expect(acceptButton(container).disabled, '轮询过一轮之后就该能点了').toBe(false)
  })

  // 静默刷新不能把人正在打的退回理由清掉——loadAcceptCard(silent) 就是为此存在的。
  it('轮询期间正在写的退回理由不会被抹掉', async () => {
    getAcceptCards.mockResolvedValue({ data: [pendingCard(SETTLED)], has_more: false })
    const { container, getByText } = render(TopicAcceptCard, {
      props: { topicId: 't1', topicStatus: 'active' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await flush()
    ;(getByText('退回').closest('button') as HTMLButtonElement).click()
    await flush()
    const input = container.querySelector('input[type="text"], textarea') as HTMLInputElement
    expect(input, '点了退回应该出现输入框').toBeTruthy()

    await vi.advanceTimersByTimeAsync(15_000)
    await flush()

    expect(container.querySelector('input[type="text"], textarea'), '输入框不该被刷没').toBeTruthy()
  })
})
