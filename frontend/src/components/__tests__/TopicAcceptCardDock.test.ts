/** 贴在输入框上方的验收横条。
 *
 * 它原来是对话末尾一张 280px 高的卡，一递上来对话就只剩几行。现在平时只有一行：
 * 这是什么、等谁、「审阅」；整张卡点开才有。这一份钉的就是这三件事，以及任务卡
 * 详情里（不贴底）整张卡照旧摊开。
 */
import type { Plugin } from 'vue'
import type { AcceptCard } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, type Pinia, setActivePinia } from 'pinia'
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

import i18n, { setLocale } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { USER_REF_DIRECTORY } from '@/lib/userRefDirectory'
import { useWorkspaceStore } from '@/stores/workspace'

let pinia: Pinia

// 句子里的人名 chip（「待 @某人 审阅」）的名字和去处来自外壳注入的目录
// （lib/userRefDirectory.ts；外壳那份在 composables/useUserRefDirectory.ts）。这里
// 没有外壳、也没有路由，而这一份要断的正是**显示名**，所以注入一个只查名册的替身：
// 名字同外壳一样查 workspace store 的成员，去处留空（没有路由，点了也去不了）。
const directory: Plugin = {
  install(app) {
    const store = useWorkspaceStore()
    app.provide(USER_REF_DIRECTORY, {
      name: (handle: string) => memberName(store.members.find((m) => m.user_handle === handle)) || null,
      target: () => null,
      navigate: () => {},
    })
  },
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

async function mountWith(cards: AcceptCard[], docked: boolean, topicStatus = 'active') {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus, docked },
    global: { plugins: [vuetify, i18n, pinia, directory] },
  })
  await flush()
  return utils
}

beforeEach(() => {
  setLocale('zh-CN')
  pinia = createPinia()
  setActivePinia(pinia)
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

  it('检查还在跑时不说在等人审阅：那时还没轮到人', async () => {
    const { container } = await mountWith(
      [
        card({
          reviewer_handle: 'bob',
          pr_number: 12,
          merge_state: {
            state: 'blocked',
            who: 'ci',
            reasons: [{ kind: 'ci_running', checks: ['CI required'], detail: '检查进行中' }],
            head_sha: 'abc',
            checked_at: null,
            since: null,
          },
        }),
      ],
      true
    )
    const bar = container.querySelector('.accept-bar')!.textContent
    expect(bar).not.toContain('待 @bob 审阅')
    expect(bar).toContain('等待检查')
  })

  it('可以合并时说在等谁审阅', async () => {
    const { container } = await mountWith(
      [
        card({
          reviewer_handle: 'bob',
          pr_number: 12,
          merge_state: {
            state: 'clean',
            who: 'human',
            reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
            head_sha: 'abc',
            checked_at: null,
            since: null,
          },
        }),
      ],
      true
    )
    expect(container.querySelector('.accept-bar')!.textContent).toContain('待 @bob 审阅')
  })

  it('等的那个人按显示名写，不按 handle', async () => {
    useWorkspaceStore().members = [{ user_handle: 'bob', role: 'member', name: 'Bob Chen' }] as never
    const { container } = await mountWith([card({ reviewer_handle: 'bob' })], true)
    const bar = container.querySelector('.accept-bar')!.textContent
    expect(bar).toContain('待 @Bob Chen 审阅')
    expect(bar).not.toContain('@bob')
  })

  it('交一次合并时，横条上写的是这次改动的标题，不是「《仓库》第 N 版」', async () => {
    const { container } = await mountWith(
      [
        card({
          change_subject: 'fix(auth): close the role-list leak',
          artifact: { id: 'a1', name: '平台代码', version: 7 },
          deliverable: { kind: 'merge', filename: null, url: null },
        }),
      ],
      true
    )
    const bar = container.querySelector('.accept-bar')!.textContent
    expect(bar).toContain('fix(auth): close the role-list leak')
    expect(bar).not.toContain('第 7 版')
  })

  it('交一份文件时，横条上写的是产物和第几版', async () => {
    const { container } = await mountWith(
      [
        card({
          change_subject: 'docs: second draft',
          artifact: { id: 'a2', name: '调研报告', version: 2 },
          deliverable: { kind: 'file', filename: 'report.pdf', url: null },
        }),
      ],
      true
    )
    expect(container.querySelector('.accept-bar')!.textContent).toContain('《调研报告》第 2 版')
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

  it('归档话题上那张已采纳的卡，标题按显示名写是谁采纳的', async () => {
    useWorkspaceStore().members = [{ user_handle: 'bob', role: 'member', name: 'Bob Chen' }] as never
    const { container } = await mountWith([card({ status: 'accepted', decided_by: 'bob' })], false, 'archived')
    const head = container.querySelector('.accept-head')!.textContent
    expect(head).toContain('@Bob Chen 已采纳')
    expect(head).not.toContain('@bob')
  })
})
