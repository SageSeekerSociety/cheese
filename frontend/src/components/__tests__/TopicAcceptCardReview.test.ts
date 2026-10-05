/** 验收卡上除「采纳」之外的那几个出口，和不该有出口的那两张脸。
 *
 * 这里钉的是卡上真能点到的几件事，以及它们背后的产品规矩：
 *
 *   1. 主分支保护：要两个人批准时，还没批的人手上有「批准」，点下去算自己那一票；
 *      一个人批就够的卡不给这颗按钮；
 *   2. 改由谁审阅：单子上只有在岗的成员（停用的队友不出现在派活的单子上），
 *      选一位就是把卡改派给他；
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

import TopicAcceptCard from '../TopicAcceptCard.vue'

import i18n, { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

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
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return utils
}

/** 卡上写着某几个字的那颗按钮。 */
function button(container: Element, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as
    | HTMLButtonElement
    | undefined
}

beforeAll(() => {
  // 「更换」是一张 VOverlay（v-menu），而 happy-dom 没有 visualViewport：不补上，
  // 单子根本挂不起来，测到的就成了「点了更换什么都没发生」。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  // 菜单量尺寸时还要读 devicePixelRatio，happy-dom 里也没有。少了这个，定位在
  // 测试收尾之后才跑，报出来的是「unhandled rejection」，比断言失败更难认。
  vi.stubGlobal('devicePixelRatio', 1)
})
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

    expect(container.textContent).toContain('你已批准')
    expect(button(container, '批准')).toBeUndefined()
  })
})

describe('改由谁审阅', () => {
  it('单子上只有在岗的成员，停用的队友不出现在派活的单子上', async () => {
    const store = useWorkspaceStore()
    store.members = [
      { user_handle: 'alice', role: 'member', name: 'Alice' },
      { user_handle: 'bob', role: 'member', name: 'Bob' },
      { user_handle: 'retired', role: 'member', name: 'Retired', active: false },
    ] as never

    const { container } = await mountWith([card({})])
    await fireEvent.click(button(container, '更换')!)
    await flush()

    const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item')).map((n) => n.textContent ?? '')
    expect(items.some((t) => t.includes('Bob') && t.includes('bob'))).toBe(true)
    expect(items.some((t) => t.includes('Retired'))).toBe(false)
  })

  it('选一位就是把卡改派给他', async () => {
    const store = useWorkspaceStore()
    store.members = [
      { user_handle: 'alice', role: 'member', name: 'Alice' },
      { user_handle: 'bob', role: 'member', name: 'Bob' },
    ] as never

    const card1 = card({})
    reassignCard.mockResolvedValue(card1)
    const { container } = await mountWith([card1])
    await fireEvent.click(button(container, '更换')!)
    await flush()

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

    expect(container.textContent).toContain('合并未完成')
    expect(container.textContent).toContain('PR #12')
    expect(container.textContent).toContain('合并被 GitHub 拒了')
    expect(button(container, '采纳')).toBeUndefined()
    expect(button(container, '退回')).toBeUndefined()
  })
})
