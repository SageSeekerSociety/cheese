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

  it.each([
    ['直接开始的题过了截止', { startMode: 'DIRECT', userDeadline: Date.now() + DAY }],
    ['申请制的题、自己的截止也过了', { startMode: 'APPLY', userDeadline: Date.now() - DAY }],
  ] as const)('%s：这一行照样写「已截止」', (_, more) => {
    const view = show({ deadline: Date.now() - DAY, joined: true, submittable: true, ...more })

    view.getByText(t('tasks.page.state.closed'))
  })
})
