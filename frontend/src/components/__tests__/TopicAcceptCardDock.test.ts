/** 贴在输入框上方的采纳横条，和「改动」页顶部那块的分工。
 *
 * 它原来是对话末尾一张 280px 高的卡，后来收成一行、点开再展开整张卡，展开的那张卡
 * 又把对话挤得只剩几行。现在横条只说现在在等什么，轮到人时在横条上退回或采纳；交的
 * 是什么、检查怎样在「改动」页顶部。这一份钉的就是这条分工，以及退回时输入框让给
 * 退回理由。
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
const rejectCard = vi.fn()

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
vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => null }))

import { AcceptPage } from './acceptHarness'

import i18n, { setLocale } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { USER_REF_DIRECTORY } from '@/lib/userRefDirectory'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

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

async function mountWith(cards: AcceptCard[], opts: { topicStatus?: string; reviewButton?: boolean } = {}) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(AcceptPage, {
    props: { topicId: 't1', topicStatus: opts.topicStatus ?? 'active', reviewButton: !!opts.reviewButton },
    global: { plugins: [vuetify, i18n, pinia, directory] },
  })
  await flush()
  return utils
}

const bar = (container: Element) => container.querySelector('.accept-bar')!.textContent ?? ''
const head = (container: Element) => container.querySelector('.review-head')?.textContent ?? ''
const buttonNamed = (container: Element, label: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)

const CHECKS_RUNNING = {
  state: 'blocked' as const,
  who: 'ci' as const,
  reasons: [{ kind: 'ci_running' as const, checks: ['CI required'], detail: '检查进行中' }],
  head_sha: 'abc',
  checked_at: null,
  since: null,
}
// 托管方报告检查的那一档（GitHub）：合并态说了算，检查没跑完就还没轮到人。
const GITHUB = {
  kind: 'github_app' as const,
  reports_checks: true,
  hosts_proposals: true,
  can_write_remote: true,
  pushes_to_external_remote: true,
  identity: 'platform' as const,
  declaration: '',
}
const CLEAN = {
  state: 'clean' as const,
  who: 'human' as const,
  reasons: [{ kind: 'no_obstacle' as const, checks: [], detail: '可以合并' }],
  head_sha: 'abc',
  checked_at: null,
  since: null,
}

beforeEach(() => {
  setLocale('zh-CN')
  pinia = createPinia()
  setActivePinia(pinia)
  getAcceptCards.mockReset()
  rejectCard.mockReset()
})

/** 打开 p1，它的成员名册已经读回来。 */
function withMembers(members: object[]) {
  seedProjects([])
  seedProject('p1', { topics: [], members: members as never, unread: {}, privateUnread: {}, notifyLevels: {} })
  useWorkspaceStore().openProject('p1')
}

describe('横条只说现在在等什么', () => {
  it('交的是什么不在横条上，在「改动」页顶部', async () => {
    const { container } = await mountWith([card({ reviewer_handle: 'bob' })])
    expect(bar(container)).toContain('待 @bob 审阅')
    expect(bar(container)).not.toContain('chore: do a thing')
    expect(head(container)).toContain('chore: do a thing')
  })

  it('检查还在跑时不说在等人审阅，也不放决定按钮：那时还没轮到人', async () => {
    const { container } = await mountWith([
      card({ reviewer_handle: 'bob', pr_number: 12, forge: GITHUB, merge_state: CHECKS_RUNNING }),
    ])
    expect(bar(container)).not.toContain('待 @bob 审阅')
    expect(bar(container)).toContain('等待检查')
    const strip = container.querySelector('.accept-bar')!
    expect(buttonNamed(strip, '退回')).toBeUndefined()
    expect(buttonNamed(strip, '采纳并完成任务')).toBeUndefined()
  })

  it('可以合并时说在等谁审阅，退回和采纳就在横条上', async () => {
    const { container } = await mountWith([card({ reviewer_handle: 'bob', pr_number: 12, merge_state: CLEAN })])
    const strip = container.querySelector('.accept-bar')!
    expect(bar(container)).toContain('待 @bob 审阅')
    expect(buttonNamed(strip, '退回')).toBeDefined()
    expect(buttonNamed(strip, '采纳并完成任务')).toBeDefined()
  })

  it('等的那个人按显示名写，不按 handle', async () => {
    withMembers([{ user_handle: 'bob', role: 'member', name: 'Bob Chen' }])
    const { container } = await mountWith([card({ reviewer_handle: 'bob' })])
    expect(bar(container)).toContain('待 @Bob Chen 审阅')
    expect(bar(container)).not.toContain('@bob')
  })

  it('等的是自己的时候说「待你审阅」', async () => {
    const { container } = await mountWith([card({ reviewer_handle: 'alice' })])
    expect(bar(container)).toContain('待你审阅')
  })

  it('点状态那半句，把 review 交出去（去「改动」页）', async () => {
    const { container, emitted } = await mountWith([card({})])
    await fireEvent.click(container.querySelector('.accept-bar__status') as HTMLElement)
    expect(emitted().review).toHaveLength(1)
  })

  it('手机上横条只放「审阅」：决定在「改动」页底部', async () => {
    const { container, emitted } = await mountWith([card({ pr_number: 12, merge_state: CLEAN })], {
      reviewButton: true,
    })
    const strip = container.querySelector('.accept-bar')!
    expect(buttonNamed(strip, '采纳并完成任务')).toBeUndefined()
    await fireEvent.click(buttonNamed(strip, '审阅')!)
    expect(emitted().review).toHaveLength(1)
  })

  it('归档话题上那张已采纳的卡，按显示名写是谁采纳的，留一个撤回入口', async () => {
    withMembers([{ user_handle: 'bob', role: 'member', name: 'Bob Chen' }])
    const { container } = await mountWith([card({ status: 'accepted', decided_by: 'bob' })], {
      topicStatus: 'archived',
    })
    expect(bar(container)).toContain('@Bob Chen 已采纳')
    expect(bar(container)).not.toContain('@bob')
    expect(buttonNamed(container, '撤回采纳')).toBeDefined()
  })
})

describe('「改动」页顶部的标题按交的东西说', () => {
  it('交一次合并时，是这次改动的标题，不是「《仓库》第 N 版」', async () => {
    const { container } = await mountWith([
      card({
        change_subject: 'fix(auth): close the role-list leak',
        artifact: { id: 'a1', name: '平台代码', version: 7 },
        deliverable: { kind: 'merge', filename: null, url: null },
      }),
    ])
    expect(head(container)).toContain('fix(auth): close the role-list leak')
    expect(head(container)).not.toContain('第 7 版')
  })

  it('交一份文件时，是产物和第几版', async () => {
    const { container } = await mountWith([
      card({
        change_subject: 'docs: second draft',
        artifact: { id: 'a2', name: '调研报告', version: 2 },
        deliverable: { kind: 'file', filename: 'report.pdf', url: null },
      }),
    ])
    expect(head(container)).toContain('《调研报告》第 2 版')
  })
})

describe('退回', () => {
  it('点退回，横条换成退回理由；送出的是写下的理由', async () => {
    const pending = card({})
    rejectCard.mockResolvedValue({ ...pending, status: 'rejected' })
    const { container, emitted } = await mountWith([pending])

    await fireEvent.click(buttonNamed(container.querySelector('.accept-bar')!, '退回')!)
    expect(emitted().rejecting.at(-1)).toEqual([true])
    const field = container.querySelector('.reject-form textarea') as HTMLTextAreaElement
    await fireEvent.update(field, '待决提示的问题修复后再提交')
    await fireEvent.click(buttonNamed(container.querySelector('.reject-form')!, '退回')!)
    await flush()

    expect(rejectCard).toHaveBeenCalledWith(pending.id, 'alice', '待决提示的问题修复后再提交', [])
  })

  it('取消退回，什么都不发，横条回来', async () => {
    const { container, emitted } = await mountWith([card({})])

    await fireEvent.click(buttonNamed(container.querySelector('.accept-bar')!, '退回')!)
    await fireEvent.click(buttonNamed(container.querySelector('.reject-form')!, '取消')!)

    expect(rejectCard).not.toHaveBeenCalled()
    expect(container.querySelector('.accept-bar')).not.toBeNull()
    expect(emitted().rejecting.at(-1)).toEqual([false])
  })
})
