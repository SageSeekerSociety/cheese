/** 机器闸门退役之后，成果待采纳框上还剩什么。
 *
 * 采纳即合并 (#296, stage 1) 退役了机器闸门：今天递的卡不会再进任何闸门状态，
 * 检查第一次真跑是在 PR 的 GitHub Actions 上。但库里退役之前的行还在，打开旧
 * 话题的人还要看得懂。所以这里断言的全是屏幕上读得到的字：
 *
 *   1. 历史卡的两张脸还在（未通过 / 未能执行），输出点开就有；
 *   2. 不再有「检查进行中」那张脸——一张转着圈的卡片是在说平台此刻正跑着什么，
 *      而平台什么也没跑；
 *   3. 今天递的卡上不出现任何闸门读数，历史卡上的那一格不许承诺未来会再跑。
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

import TopicAcceptCard from '../TopicAcceptCard.vue'

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
    has_external_checks: false,
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
})

describe('历史闸门卡还要能看', () => {
  it('检查未通过的旧卡显示未通过，输出点开就有', async () => {
    const { container, getByText } = await mountWith([
      card({ status: 'gate_failed', gate_output: 'ruff: E501 line too long' }),
    ])

    expect(container.textContent).toContain('平台检查未通过')
    expect(container.textContent).not.toContain('ruff: E501 line too long')

    await fireEvent.click(getByText('查看输出'))
    await flush()
    expect(container.textContent).toContain('ruff: E501 line too long')
  })

  it('检查没跑成的旧卡跟「未通过」分开显示，并说明它对代码没有结论', async () => {
    const { container } = await mountWith([card({ status: 'gate_blocked', gate_output: 'docker: not found' })])

    expect(container.textContent).toContain('平台检查未能执行')
    expect(container.textContent).not.toContain('平台检查未通过')
    expect(container.textContent).toContain('没有结论')
  })
})

describe('闸门退役之后不该再出现的东西', () => {
  it('闸门运行中那张脸没有了——平台不会再跑任何检查', async () => {
    const { container } = await mountWith([card({ status: 'pending_gate' })])

    expect(container.textContent).not.toContain('进行中')
    expect(container.querySelector('.v-progress-circular')).toBeNull()
  })

  it('今天递的卡上没有任何闸门读数', async () => {
    const { container } = await mountWith([card({ status: 'pending' })])

    expect(container.textContent).toContain('成果待采纳')
    expect(container.textContent).not.toContain('平台检查')
  })

  it('历史卡的「已通过」读数不承诺以后还会再跑一次', async () => {
    const { container } = await mountWith([card({ status: 'pending', gate_passed_at: '2026-08-01T00:00:00Z' })])

    expect(container.textContent).toContain('平台检查已通过')
    expect(container.textContent).toContain('未运行测试')
    expect(container.textContent).not.toContain('授权后开始')
  })
})
