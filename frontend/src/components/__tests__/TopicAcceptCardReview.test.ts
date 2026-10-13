/** 采纳卡上除「采纳」之外的那几个出口，和不该有出口的那两张脸。
 *
 * 这里钉的是卡上真能点到的几件事，以及它们背后的产品规矩：
 *
 *   1. 主分支保护：要两个人批准时，还没批的人手上有「批准」，点下去算自己那一票；
 *      一个人批就够的卡不给这颗按钮；
 *   2. 改由他人审阅（「更多操作」里）：单子上只有人——AI 队友采纳不了，不出现在
 *      单子上——选一位就是把卡改派给他；
 *   3. 采纳可撤销：归档话题上的已采纳卡给「撤回采纳」；话题还在进行中时，那张
 *      已采纳的卡不占位置；
 *   4. 已采纳、合并还没走完的那张脸是只读的：PR 在卡上，没有采纳也没有退回。
 */
import type { AcceptCard, MergeStateInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const approveCard = vi.fn()
const reassignCard = vi.fn()
const revokeCard = vi.fn()

// 任务页的卡旁边还有这件任务的审阅意见；这里测的是卡，意见是空的。
vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    approveCard: (...a: unknown[]) => approveCard(...a),
    reassignCard: (...a: unknown[]) => reassignCard(...a),
    revokeCard: (...a: unknown[]) => revokeCard(...a),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => null }))

import { AcceptPage, chooseMore, stubOverlayGlobals } from './acceptHarness'

import i18n, { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

function mergeState(): MergeStateInfo {
  return {
    state: 'clean',
    who: 'human',
    reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
    head_sha: null,
    checked_at: null,
    since: null,
  }
}

let seq = 0
function card(over: Partial<AcceptCard>): AcceptCard {
  seq += 1
  return {
    id: `card-${seq}`,
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
    merge_state: mergeState(),
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(cards: AcceptCard[], topicStatus = 'active') {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(AcceptPage, {
    props: { topicId: 't1', taskId: 'k1', topicStatus },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return utils
}

/** 字正好是这几个字的那颗按钮。 */
function button(container: Element, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label) as
    | HTMLButtonElement
    | undefined
}
/** 字里带着这几个字的那颗按钮（信号、横条这种整句的按钮）。 */
function buttonWith(container: Element, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as
    | HTMLButtonElement
    | undefined
}

beforeAll(() => stubOverlayGlobals(vi))
afterAll(() => vi.unstubAllGlobals())

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  approveCard.mockReset()
  reassignCard.mockReset()
  revokeCard.mockReset()
})

describe('要几个人批准才合得进去', () => {
  it('还没批的人手上有「批准」，点下去算自己那一票', async () => {
    const card1 = card({ approvals_required: 2, approvals: ['bob'] })
    approveCard.mockResolvedValue(card1)
    const { container } = await mountWith([card1])

    expect(container.textContent).toContain('1/2')

    await fireEvent.click(buttonWith(container, '1/2')!)
    await fireEvent.click(button(container, '批准')!)
    await flush()

    expect(approveCard).toHaveBeenCalledWith(card1.id, 'alice')
  })

  it('一个人批就够的卡不给这颗按钮 —— 那里本来就没有要等的票', async () => {
    const { container } = await mountWith([card({ approvals_required: 1 })])

    expect(button(container, '批准')).toBeUndefined()
  })

  it('我已经批过的卡写「你已批准」，不再给我一颗按钮', async () => {
    const { container } = await mountWith([card({ approvals_required: 2, approvals: ['alice'] })])
    await fireEvent.click(buttonWith(container, '1/2')!)

    expect(container.textContent).toContain('你已批准')
    expect(button(container, '批准')).toBeUndefined()
  })
})

/** 打开 p1，它的成员名册已经读回来。 */
function withMembers(members: object[]) {
  seedProjects([])
  seedProject('p1', { topics: [], members: members as never, unread: {}, privateUnread: {}, notifyLevels: {} })
  useWorkspaceStore().openProject('p1')
}

describe('改由谁审阅', () => {
  it('单子上只有人：AI 队友不管在不在岗都不出现', async () => {
    withMembers([
      { user_handle: 'alice', role: 'member', name: 'Alice' },
      { user_handle: 'bob', role: 'member', name: 'Bob' },
      { user_handle: 'cheese-0a1b2c3d4e5f', role: 'member', name: '小苔', source: 'agent', agent: true },
      {
        user_handle: 'cheese-9f8e7d6c5b4a',
        role: 'member',
        name: 'Retired',
        source: 'agent',
        agent: true,
        active: false,
      },
    ])

    const { container } = await mountWith([card({})])
    await chooseMore(container, '改由他人审阅')

    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).map((n) => n.textContent ?? '')
    expect(items.some((t) => t.includes('Bob') && t.includes('bob'))).toBe(true)
    expect(items.some((t) => t.includes('小苔'))).toBe(false)
    expect(items.some((t) => t.includes('Retired'))).toBe(false)
  })

  it('选一位就是把卡改派给他', async () => {
    withMembers([
      { user_handle: 'alice', role: 'member', name: 'Alice' },
      { user_handle: 'bob', role: 'member', name: 'Bob' },
    ])

    const card1 = card({})
    reassignCard.mockResolvedValue(card1)
    const { container } = await mountWith([card1])
    await chooseMore(container, '改由他人审阅')

    const bob = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).find((n) =>
      n.textContent?.includes('Bob')
    ) as HTMLElement
    await fireEvent.click(bob)
    await flush()

    expect(reassignCard).toHaveBeenCalledWith(card1.id, 'bob')
  })
})

describe('已采纳的话题', () => {
  it('归档话题上的卡给「撤回采纳」，点下去撤回这次采纳', async () => {
    const accepted = card({ status: 'accepted', decided_by: 'bob' })
    revokeCard.mockResolvedValue(accepted)
    const { container } = await mountWith([accepted], 'archived')

    expect(container.textContent).toContain('已采纳')

    await fireEvent.click(button(container, '撤回采纳')!)
    await flush()

    expect(revokeCard).toHaveBeenCalledWith(accepted.id, 'alice')
  })

  it('话题还在进行中时，已采纳的卡不占位置', async () => {
    const { container } = await mountWith([card({ status: 'accepted', decided_by: 'bob' })], 'active')

    expect(container.querySelector('.accept-fold')).toBeNull()
  })
})

describe('已采纳、合并还没走完的那张脸', () => {
  it('只读：PR 在卡上，没有采纳也没有退回', async () => {
    const { container } = await mountWith([
      card({
        status: 'pr_open',
        decided_by: 'bob',
        pr_number: 12,
        pr_url: 'https://github.com/o/r/pull/12',
        pr_head_sha: 'abcdef1234',
        note: '合并被 GitHub 拒了',
        note_level: 'error',
      }),
    ])
    // 历史卡：点横条才在上面展开当年那张卡。
    await fireEvent.click(buttonWith(container, '合并未完成')!)
    await flush()

    expect(container.textContent).toContain('合并未完成')
    expect(container.textContent).toContain('PR #12')
    expect(container.textContent).toContain('合并被 GitHub 拒了')
    expect(button(container, '采纳')).toBeUndefined()
    expect(button(container, '退回')).toBeUndefined()
  })
})
