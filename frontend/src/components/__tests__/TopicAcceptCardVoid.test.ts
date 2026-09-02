/** 作废这张卡：一张走不下去的验收卡，界面上唯一的出口。
 *
 * `pending_gate` / `conflict` / `pr_open` 被 accept / reject / revoke / reassign
 * 四条路由全部拒绝，而一张活着的卡本身又让话题递不出下一张——所以踩中其中任何
 * 一个，整个房间从此交付不了。后端的 `void` 从 2026-08-11 就在，界面上一直没有
 * 任何东西调它（真实案例：PR #545 被人工关闭，卡永久停在 `pr_open`）。
 *
 * 这里断言的是人在屏幕上能做到什么：三种活着的状态下这条出口都点得到，点下去
 * 带着理由走的是 `void`，后端拒绝时它说的原因要能被看见。
 */
import type { AcceptCard } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const voidAcceptCard = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    voidAcceptCard: (...a: unknown[]) => voidAcceptCard(...a),
  }
})

import TopicAcceptCard from '../TopicAcceptCard.vue'

import { useWorkspaceStore } from '@/stores/workspace'

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
    created_at: '2026-08-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(cards: AcceptCard[]) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active' },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  voidAcceptCard.mockReset()
  voidAcceptCard.mockResolvedValue(card({ status: 'revoked' }))
})

describe('每一张还活着的卡都点得到作废', () => {
  it.each([
    ['待采纳', 'pending'],
    ['合并冲突', 'conflict'],
    ['交付中', 'pr_open'],
  ])('%s 的卡上有作废入口', async (_label, status) => {
    const { getByText } = await mountWith([
      card({ status: status as AcceptCard['status'], decided_by: 'alice', pr_number: 545, pr_url: 'https://x/545' }),
    ])

    expect(getByText('作废这张卡')).toBeTruthy()
  })
})

describe('点下去会发生什么', () => {
  it('要先展开、填理由、再确认——不是一个能顺手点到的按钮', async () => {
    const stuck = card({ status: 'pending' })
    const { container, getByText, getByLabelText } = await mountWith([stuck])

    expect(container.textContent).not.toContain('确认作废')

    await fireEvent.click(getByText('作废这张卡'))
    await flush()
    await fireEvent.update(getByLabelText('理由'), 'PR 已经在 GitHub 上关掉了')
    await fireEvent.click(getByText('确认作废'))
    await flush()

    expect(voidAcceptCard).toHaveBeenCalledWith(stuck.id, 'PR 已经在 GitHub 上关掉了')
  })

  it('说清后果：卡到此为止，话题可以重新递卡', async () => {
    const { container, getByText } = await mountWith([card({ status: 'pr_open', decided_by: 'alice' })])

    await fireEvent.click(getByText('作废这张卡'))
    await flush()

    expect(container.textContent).toContain('不再推进')
    expect(container.textContent).toContain('重新递卡')
  })

  it('卡骑着一个 PR 的时候，说明平台不会替你关掉它', async () => {
    const { container, getByText } = await mountWith([
      card({ status: 'pr_open', decided_by: 'alice', pr_number: 545, pr_url: 'https://x/545' }),
    ])

    await fireEvent.click(getByText('作废这张卡'))
    await flush()

    expect(container.textContent).toContain('平台不会替你关闭它')
  })

  it('作废成功后这个框整个消失，话题回到能重新递卡的样子', async () => {
    const { container, getByText } = await mountWith([card({ status: 'pr_open', decided_by: 'alice' })])
    expect(container.textContent).toContain('交付中')

    getAcceptCards.mockResolvedValue({ data: [card({ status: 'revoked' })], has_more: false })
    await fireEvent.click(getByText('作废这张卡'))
    await flush()
    await fireEvent.click(getByText('确认作废'))
    await flush()

    expect(container.textContent).not.toContain('交付中')
    expect(container.textContent).not.toContain('作废这张卡')
  })

  it('后端拒绝时说的原因要被看见，不能只说一句作废失败', async () => {
    voidAcceptCard.mockRejectedValue(new Error('只有这张卡的验收人或项目 owner / 组长能作废它'))
    const { getByText } = await mountWith([card({ status: 'conflict' })])

    await fireEvent.click(getByText('作废这张卡'))
    await flush()
    await fireEvent.click(getByText('确认作废'))
    await flush()

    expect(useWorkspaceStore().error).toBe('只有这张卡的验收人或项目 owner / 组长能作废它')
  })
})

describe('发给后端的那个请求', () => {
  it('打的是 /void，而且不带 decided_by——作废人只能来自登录态', async () => {
    // 真的那个 `voidAcceptCard`（上面 mock 掉的是给组件用的那份），配一个假的
    // fetch：请求体里多一个 `decided_by` 就等于让调用方自称是谁，而作废跟采纳
    // /放行一样是授权动作，后端只认 session。
    const api = await vi.importActual<typeof import('@/api')>('@/api')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ code: 200, data: { id: 'card-9', status: 'revoked' } }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.voidAcceptCard('card-9', '不合了')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/accept-cards/card-9/void')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ note: '不合了' })

    vi.unstubAllGlobals()
  })
})
