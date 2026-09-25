/** 贴在输入框上方的验收横条。
 *
 * 它原来是对话末尾一张 280px 高的卡，一递上来对话就只剩几行。现在平时只有一行：
 * 这是什么、等谁、「审阅」；整张卡点开才有。这一份钉的就是这三件事，以及任务卡
 * 详情里（不贴底）整张卡照旧摊开。
 */
import type { AcceptCard } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
  }
})
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => null }))

import TopicAcceptCard from '../TopicAcceptCard.vue'

import { setLocale } from '@/i18n'

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
    pr_number: null,
    forge: {
      kind: 'forgejo',
      reports_checks: false,
      hosts_proposals: false,
      can_write_remote: false,
      pushes_to_external_remote: false,
      identity: 'platform',
      declaration: 'ℹ️ 本项目未接外部仓库：采纳即合并进平台仓库的 main（无提案页、无外部 CI）',
    },
    pr_url: null,
    // 平台 lane 的常态 (#718)：没有信号，who 恒 human，采纳纯是人的判断。
    merge_state: {
      state: 'unknown',
      who: 'human',
      reasons: [{ kind: 'no_signal', checks: [], detail: '还没有信号' }],
      head_sha: null,
      checked_at: null,
      since: null,
    },
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(cards: AcceptCard[], docked: boolean) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active', docked },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
})

describe('贴底的时候', () => {
  it('平时只有一行：是什么、等谁，卡里的细节不在屏幕上', async () => {
    const { container } = await mountWith([card({ reviewer_handle: 'bob' })], true)
    const bar = container.querySelector('.accept-bar')!
    expect(bar.textContent).toContain('改动')
    expect(bar.textContent).toContain('待 @bob 审阅')
    expect(container.textContent).not.toContain('chore: do a thing')
  })

  it('等的是自己的时候说「待你审阅」', async () => {
    const { container } = await mountWith([card({ reviewer_handle: 'alice' })], true)
    expect(container.querySelector('.accept-bar')!.textContent).toContain('待你审阅')
  })

  it('点开横条才是整张卡，再点收回去', async () => {
    const { container } = await mountWith([card({})], true)
    const toggle = container.querySelector('.accept-bar__toggle') as HTMLElement
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    await fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(container.textContent).toContain('chore: do a thing')

    await fireEvent.click(toggle)
    expect(container.textContent).not.toContain('chore: do a thing')
  })

  it('「审阅」在横条上，点下去把 review 交出去；展开的卡里不再放第二颗', async () => {
    const { container, emitted, getAllByRole } = await mountWith([card({})], true)
    await fireEvent.click(container.querySelector('.accept-bar__toggle') as HTMLElement)
    expect(getAllByRole('button', { name: '审阅' })).toHaveLength(1)
    await fireEvent.click(getAllByRole('button', { name: '审阅' })[0])
    expect(emitted().review).toHaveLength(1)
  })
})

describe('不贴底的时候（任务卡详情里）', () => {
  it('没有横条，整张卡直接摊开，标题在卡自己身上', async () => {
    const { container } = await mountWith([card({})], false)
    expect(container.querySelector('.accept-bar')).toBeNull()
    expect(container.textContent).toContain('改动')
    expect(container.textContent).toContain('chore: do a thing')
  })
})
