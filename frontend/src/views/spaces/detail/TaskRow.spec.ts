/** 题目列表里的一行：参与的数只算申请通过的，团队题数的是队伍；被拒的申请不算「我的」。 */
import type { Task } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
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

  it('申请截止之后通过、自己的截止还没到：这一行写在做，不写「已截止」', () => {
    const view = show({
      startMode: 'APPLY',
      deadline: Date.now() - DAY,
      joined: true,
      submittable: true,
      userDeadline: Date.now() + DAY,
    })

    view.getByText(t('spaces.detail.tasks.row.mine'))
    expect(view.queryByText(t('tasks.page.state.closed'))).toBeNull()
  })

  // 一份再也动不了的领取不标「在做」（Zhifei 2026-10-26），和题目页标题旁同一条判据。
  it.each([
    ['直接开始的题过了截止', { startMode: 'DIRECT', deadline: Date.now() - DAY, userDeadline: Date.now() + DAY }],
    ['题目已结束', { endedAt: Date.now() - DAY, userDeadline: Date.now() + DAY }],
  ] as const)('%s：这一行写「已截止」，不标「在做」', (_, more) => {
    const view = show({ joined: true, submittable: true, ...more })

    view.getByText(t('tasks.page.state.closed'))
    expect(view.queryByText(t('spaces.detail.tasks.row.mine'))).toBeNull()
  })

  it.each([
    ['申请制的题、题目和我的截止都过了', Date.now() - 2 * DAY],
    ['申请制的题还在收申请', Date.now() + 7 * DAY],
  ])('%s、我自己的截止过了：这一行只写「已过期」，不标「在做」，不写「已截止」「可申请」', (_, deadline) => {
    const view = show({
      startMode: 'APPLY',
      deadline,
      joined: true,
      submittable: true,
      userDeadline: Date.now() - DAY,
    })

    view.getByText(t('tasks.page.state.expired'))
    expect(view.queryByText(t('spaces.detail.tasks.row.mine'))).toBeNull()
    expect(view.queryByText(t('tasks.page.state.closed'))).toBeNull()
    expect(view.queryByText(t('tasks.page.state.open'))).toBeNull()
  })

  it('没有领取的人：还在收申请的题写「可申请」', () => {
    const view = show({ startMode: 'APPLY', deadline: Date.now() + 7 * DAY })

    view.getByText(t('tasks.page.state.open'))
  })
})
