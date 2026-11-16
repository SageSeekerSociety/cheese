// @vitest-environment jsdom
// 「领取者」页签：一张表里批领取申请、评审提交。量的是出题人能说出来的几条规矩：
// 待批的申请才有批准/拒绝；交了没判的才有评审；被拒的申请不算领取者；拿不到名单就说为什么。
//
// 这份用 jsdom 而不是仓库默认的 happy-dom：表格排序那一组用例要「点表头 → Vue 重排
// 已经画出来的行」，而 happy-dom 13 的 `insertBefore` 在把一个已有的子节点挪到某个
// 兄弟前面时会把它复制一份（A B C → A B C A），jsdom 与浏览器一致。别把那一组用例
// 挪到 happy-dom 下去跑，会看到不存在的多出來一行。
import type { Latest } from './RosterView.vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import RosterView from './RosterView.vue'

import i18n from '@/i18n'

const DAY = 86_400_000
const t = (key: string) => i18n.global.t(key)

const TASK = { id: 42, submitterType: 'USER', requireRealName: false, defaultDeadline: 7 }

function claim(id: number, name: string, approved: string, daysAgo: number) {
  return {
    id,
    member: { id: 100 + id, name, intro: '', avatarId: null },
    createdAt: Date.now() - daysAgo * DAY,
    updatedAt: 0,
    deadline: null,
    approved,
  }
}

function mount(
  opts: {
    participants?: unknown[]
    latest?: Map<number, Latest>
    denied?: boolean
    failed?: boolean
    failureReason?: string | null
  } = {}
) {
  return render(RosterView, {
    props: {
      taskData: TASK as never,
      participants: (opts.participants ?? []) as never,
      latestByParticipant: opts.latest ?? new Map(),
      loading: false,
      denied: opts.denied ?? false,
      failed: opts.failed ?? false,
      failureReason: opts.failureReason ?? null,
      busyId: null,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

function row(view: ReturnType<typeof mount>, name: string): HTMLElement {
  const cell = view.getAllByText(name)[0]
  return cell.closest('tr') as HTMLElement
}

const PEOPLE = [
  claim(1, '林小满', 'NONE', 0),
  claim(2, '周舟', 'APPROVED', 1),
  claim(3, '许一', 'APPROVED', 2),
  claim(4, '陈二', 'DISAPPROVED', 3),
]
// 接口对还没判的那一版回的是 `{ reviewed: false }`，不是空。
const LATEST = new Map<number, Latest>([
  [2, { submissionId: 501, version: 2, createdAt: Date.now(), review: { reviewed: false } as never }],
])

describe('领取者页签', () => {
  // 对话框（v-dialog）在 happy-dom 里要这几样才画得出来。
  beforeAll(() => {
    if (!('ResizeObserver' in globalThis)) {
      ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      }
    }
    if (!globalThis.visualViewport) {
      ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
        width: 1024,
        height: 768,
        offsetLeft: 0,
        offsetTop: 0,
        scale: 1,
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent: () => false,
      }
    }
  })

  afterEach(() => cleanup())

  it('待批的申请：批准与拒绝，报出的是那一个人', async () => {
    const view = mount({ participants: PEOPLE, latest: LATEST })

    await fireEvent.click(within(row(view, '林小满')).getByText(t('tasks.roster.approve')))
    expect(view.emitted('approve')).toEqual([[1]])
  })

  it('拒绝要先在对话框里确认，带上填的原因', async () => {
    const view = mount({ participants: PEOPLE, latest: LATEST })

    await fireEvent.click(within(row(view, '林小满')).getByText(t('tasks.roster.reject')))
    expect(view.emitted('reject')).toBeUndefined()

    const dialog = await waitFor(() => {
      const el = document.querySelector('.v-overlay--active .v-card') as HTMLElement | null
      expect(el).not.toBeNull()
      return el!
    })
    await fireEvent.update(within(dialog).getByRole('textbox'), '人数够了')
    await fireEvent.click(within(dialog).getByRole('button', { name: t('tasks.roster.reject') }))
    expect(view.emitted('reject')).toEqual([[1, '人数够了']])
  })

  it('交了没判的那一行才有评审；没交的没有', async () => {
    const view = mount({ participants: PEOPLE, latest: LATEST })

    await fireEvent.click(within(row(view, '周舟')).getByText(t('tasks.roster.review')))
    expect(view.emitted('review')).toEqual([[{ id: 2, name: '周舟' }]])
    expect(within(row(view, '许一')).queryByText(t('tasks.roster.review'))).toBeNull()
    expect(within(row(view, '林小满')).queryByText(t('tasks.roster.review'))).toBeNull()
  })

  it('判过的提交按判的结果算：通过的进「已通过」，没判的不算', async () => {
    const latest = new Map<number, Latest>([
      [2, { submissionId: 501, version: 2, createdAt: Date.now(), review: { reviewed: false } as never }],
      [
        3,
        {
          submissionId: 502,
          version: 1,
          createdAt: Date.now(),
          review: { reviewed: true, detail: { accepted: true, score: 0, comment: '' } },
        },
      ],
    ])
    const view = mount({ participants: PEOPLE, latest })

    const tab = view.getAllByRole('tab').find((b) => b.textContent?.includes(t('tasks.roster.passed')))!
    await fireEvent.click(tab)
    expect(view.getByText('许一')).toBeTruthy()
    expect(view.queryByText('周舟')).toBeNull()
  })

  it('被拒的申请不在「全部」里', () => {
    const view = mount({ participants: PEOPLE, latest: LATEST })

    expect(view.queryByText('陈二')).toBeNull()
    expect(view.getByText('林小满')).toBeTruthy()
  })

  it('筛「待评审」只剩交了没判的人', async () => {
    const view = mount({ participants: PEOPLE, latest: LATEST })

    const tab = view.getAllByRole('tab').find((b) => b.textContent?.includes(t('tasks.roster.reviewPending')))!
    await fireEvent.click(tab)
    expect(view.getByText('周舟')).toBeTruthy()
    expect(view.queryByText('林小满')).toBeNull()
    expect(view.queryByText('许一')).toBeNull()
  })

  it('拿不到名单：不画表', () => {
    const view = mount({ failed: true, failureReason: '服务端打盹了' })

    expect(view.container.querySelector('table')).toBeNull()
  })

  it('不给你看：说没权限，不摆重试', () => {
    const view = mount({ denied: true })

    expect(view.getByText(t('tasks.roster.denied'))).toBeTruthy()
    expect(view.queryByText(t('global.loadError.retry'))).toBeNull()
  })

  // 出题人看到「没权限」会去查自己的权限；这一次没读到该做的是再试一次。
  it('这次没读到：说没读出来并给重试，不说没权限', () => {
    const view = mount({ failed: true, failureReason: '服务端打盹了' })

    expect(view.queryByText(t('tasks.roster.denied'))).toBeNull()
    expect(view.getByText(t('tasks.roster.loadFailed'))).toBeTruthy()
    expect(view.getByText('服务端打盹了')).toBeTruthy()
    expect(view.getByText(t('global.loadError.retry'))).toBeTruthy()
    expect(view.queryByText(t('tasks.roster.empty'))).toBeNull()
  })
})

// ── 额度那两列 ────────────────────────────────────────────────────────────────
//
// 「额度用量」谁都有（题目不按额度计分时它只是一个参考数）；「额度得分」只有评分要点里
// 有额度那一行的题才画。两列都能点表头排序 —— 升序就是「用得少的在前」。

function mountWith(rubric: unknown[], participants: unknown[]) {
  return render(RosterView, {
    props: {
      taskData: { ...TASK, rubric } as never,
      participants: participants as never,
      latestByParticipant: new Map(),
      loading: false,
      denied: false,
      failed: false,
      failureReason: null,
      busyId: null,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const member = (id: number, name: string, credits: number, usage: unknown = undefined) => ({
  ...claim(id, name, 'APPROVED', id),
  usage: usage === undefined ? { credits } : usage,
})

const USAGE_LINE = { index: 1, points: 20, mode: 'threshold', full: 0, zero: 100 }

/** The names in the order the table draws them (the first cell of each row). */
function drawnNames(view: ReturnType<typeof mountWith>): string[] {
  return [...view.container.querySelectorAll('.rs__name')].map((el) => el.textContent ?? '')
}

describe('名单里的额度', () => {
  afterEach(() => cleanup())

  it('题目没有额度那一行时也画「额度用量」，但不画「额度得分」', () => {
    const view = mountWith(
      [{ item: '正确性', points: 100, criteria: '照做', stage: null }],
      [member(1, '林小满', 1200), member(2, '周舟', 400)]
    )

    expect(view.getByText(t('tasks.roster.col.credits'))).toBeTruthy()
    expect(view.queryByText(t('tasks.roster.col.usageScore'))).toBeNull()
    expect(view.getByText('1,200')).toBeTruthy()
    expect(view.getByText('400')).toBeTruthy()
  })

  it('有额度那一行时「额度得分」写着分和满分；没交的说「交了才算」', () => {
    const view = mountWith(
      [
        { item: '正确性', points: 80, criteria: '照做', stage: null },
        {
          item: '额度用量',
          points: 20,
          criteria: '',
          stage: null,
          kind: 'usage',
          config: { mode: 'threshold', full: 0, zero: 100 },
        },
      ],
      [
        member(1, '林小满', 40, { credits: 40, line: USAGE_LINE, score: 12, final: true }),
        member(2, '周舟', 10, { credits: 10, line: USAGE_LINE, score: null, final: true }),
      ]
    )

    expect(view.getByText(t('tasks.roster.col.usageScore'))).toBeTruthy()
    expect(view.container.textContent).toContain(t('tasks.roster.usageOf', { score: 12, points: 20 }))
    expect(view.getByText(t('tasks.roster.usageNotYet'))).toBeTruthy()
  })

  it('排名的暂定分标「暂定」', () => {
    const view = mountWith(
      [{ item: '额度用量', points: 100, criteria: '', stage: null, kind: 'usage', config: { mode: 'rank' } }],
      [
        member(1, '林小满', 40, {
          credits: 40,
          line: { ...USAGE_LINE, points: 100, mode: 'rank' },
          score: 50,
          final: false,
        }),
      ]
    )

    expect(view.getByText(t('tasks.roster.tentative'))).toBeTruthy()
  })

  it('点「额度用量」表头：升序把用得少的排在前面', async () => {
    const view = mountWith([], [member(1, '林小满', 1200), member(2, '周舟', 400), member(3, '许一', 800)])

    await fireEvent.click(view.getByRole('button', { name: new RegExp(t('tasks.roster.col.credits')) }))

    expect(drawnNames(view)).toEqual(['周舟', '许一', '林小满'])
  })

  it('点两次换成降序', async () => {
    const view = mountWith([], [member(1, '林小满', 1200), member(2, '周舟', 400)])

    const head = view.getByRole('button', { name: new RegExp(t('tasks.roster.col.credits')) })
    await fireEvent.click(head)
    await fireEvent.click(head)

    expect(drawnNames(view)).toEqual(['林小满', '周舟'])
  })

  it('按「额度得分」排：没算出来的（交了才算）排最后', async () => {
    const view = mountWith(
      [
        {
          item: '额度用量',
          points: 100,
          criteria: '',
          stage: null,
          kind: 'usage',
          config: { mode: 'threshold', full: 0, zero: 100 },
        },
      ],
      [
        member(1, '林小满', 40, { credits: 40, line: { ...USAGE_LINE, points: 100 }, score: null, final: true }),
        member(2, '周舟', 10, { credits: 10, line: { ...USAGE_LINE, points: 100 }, score: 90, final: true }),
        member(3, '许一', 80, { credits: 80, line: { ...USAGE_LINE, points: 100 }, score: 20, final: true }),
      ]
    )

    await fireEvent.click(view.getByRole('button', { name: new RegExp(t('tasks.roster.col.usageScore')) }))

    expect(drawnNames(view)).toEqual(['许一', '周舟', '林小满'])
  })
})
