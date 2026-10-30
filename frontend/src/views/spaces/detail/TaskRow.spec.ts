/** 题目列表里的一行：参与的数只算申请通过的，团队题数的是队伍；被拒的申请不算「我的」。 */
import type { Task } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TaskRow from './TaskRow.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const t = i18n.global.t

function show(overrides: Partial<Task>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const task = {
    id: 5,
    name: '小组项目：简易数据库索引',
    intro: '',
    topics: [],
    approved: 'APPROVED',
    deadline: null,
    participantLimit: 0,
    submitterType: 'TEAM',
    participants: { total: 5, approved: 2, examples: [] },
    creator: { id: 1, username: 'alice', nickname: 'Alice' },
    createdAt: 1,
    ...overrides,
  } as unknown as Task
  return render(TaskRow, {
    props: { task, to: '/' },
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
}

describe('题目列表的一行', () => {
  it('团队题：数的是申请通过的队伍', () => {
    const view = show({})

    view.getByText(t('spaces.detail.tasks.row.claimedTeams', { n: 2 }))
  })

  it('团队题有上限：写成「通过的队伍 / 上限」', () => {
    const view = show({ participantLimit: 4 })

    view.getByText(t('spaces.detail.tasks.row.claimedTeamsOf', { n: 2, limit: 4 }))
  })

  it('团队的申请被拒了：这一行不标成我的', () => {
    const view = show({ joined: false, joinedTeams: [{ id: 9, name: '红队' }] as never })

    expect(view.queryByText(t('spaces.detail.tasks.row.applied'))).toBeNull()
    expect(view.queryByText(t('spaces.detail.tasks.row.mine'))).toBeNull()
  })

  it('团队的申请还在等：标「已申请」', () => {
    const view = show({ joined: true, submittable: false, joinedTeams: [{ id: 9, name: '红队' }] as never })

    view.getByText(t('spaces.detail.tasks.row.applied'))
  })

  const DAY = 86_400_000
  const at = (ms: number) => dayjs(ms).format(t('tasks.page.dateFormat'))

  // 我手上有一份的题（Zhifei 2026-10-26）：这一行说的是我这一份——标签是我的状态，日期是我自己的
  // 提交截止；题目对别人开着还是截止，这一行不说。
  describe('我手上有一份的题', () => {
    const MINE = Date.now() + 5 * DAY

    it.each([
      ['申请制、申请截止过了', { startMode: 'APPLY', deadline: Date.now() - DAY }],
      ['申请制、还在收申请', { startMode: 'APPLY', deadline: Date.now() + 7 * DAY }],
      ['直接开始、题目截止过了', { startMode: 'DIRECT', deadline: Date.now() - DAY }],
    ] as const)('%s、我的截止还没到：标「在做」，写「提交截止」和我的那一天，不写题目的状态', (_, more) => {
      const view = show({ joined: true, submittable: true, userDeadline: MINE, ...more })

      view.getByText(t('spaces.detail.tasks.row.mine'))
      view.getByText(t('spaces.detail.tasks.row.submitDeadline', { date: at(MINE) }))
      for (const key of ['open', 'applyClosed', 'closed', 'openToStart'])
        expect(view.queryByText(t(`tasks.page.state.${key}`))).toBeNull()
    })

    it('我自己的截止过了：只标「已过期」，不标「在做」', () => {
      const mine = Date.now() - DAY
      const view = show({
        startMode: 'APPLY',
        deadline: Date.now() + 7 * DAY,
        joined: true,
        submittable: true,
        userDeadline: mine,
      })

      view.getByText(t('spaces.detail.tasks.row.expired'))
      view.getByText(t('spaces.detail.tasks.row.submitDeadline', { date: at(mine) }))
      expect(view.queryByText(t('spaces.detail.tasks.row.mine'))).toBeNull()
      expect(view.queryByText(t('tasks.page.state.open'))).toBeNull()
    })

    it('截止过了但已经判了通过：标「已通过」，不标「已过期」', () => {
      const view = show({
        startMode: 'APPLY',
        joined: true,
        submittable: true,
        userDeadline: Date.now() - DAY,
        myClaimStatus: 'PASSED',
      })

      view.getByText(t('spaces.detail.tasks.row.passed'))
      expect(view.queryByText(t('spaces.detail.tasks.row.expired'))).toBeNull()
    })

    it('申请还在等：标「已申请」，日期是申请截止', () => {
      const closes = Date.now() + 7 * DAY
      const view = show({ startMode: 'APPLY', deadline: closes, joined: true, submittable: false })

      view.getByText(t('spaces.detail.tasks.row.applied'))
      view.getByText(t('spaces.detail.tasks.row.applyDeadline', { date: at(closes) }))
    })
  })

  // 没有领取的人看到的是题目自己的状态，日期说清是哪一个截止。
  describe('我手上没有的题', () => {
    it.each([
      ['申请制、还在收申请', 'APPLY', Date.now() + 7 * DAY, 'open', 'spaces.detail.tasks.row.applyDeadline'],
      ['申请制、申请截止过了', 'APPLY', Date.now() - DAY, 'applyClosed', 'spaces.detail.tasks.row.applyDeadline'],
      ['直接开始、截止过了', 'DIRECT', Date.now() - DAY, 'closed', 'spaces.detail.tasks.row.deadline'],
      ['直接开始、还开着', 'DIRECT', Date.now() + 7 * DAY, 'openToStart', 'spaces.detail.tasks.row.deadline'],
    ] as const)('%s：写题目自己的状态和说清是哪一个的截止', (_, startMode, deadline, state, label) => {
      const view = show({ startMode, deadline })

      view.getByText(t(`tasks.page.state.${state}`))
      view.getByText(t(label, { date: at(deadline) }))
      expect(view.queryByText(t('spaces.detail.tasks.row.mine'))).toBeNull()
    })
  })
})
