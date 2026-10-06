// 「领取者」页签：一张表里批领取申请、评审提交。量的是出题人能说出来的几条规矩：
// 待批的申请才有批准/拒绝；交了没判的才有评审；被拒的申请不算领取者；拿不到名单就说为什么。
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
