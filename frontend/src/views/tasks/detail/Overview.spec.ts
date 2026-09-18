// 这一页有四张卡加三种「不能参与」的提示，翻完最容易漏掉提示里的分支。
// 断言三件事：中文原文还在、英文整页无汉字、英文下品牌名那条 i18n-t 插槽接上了
// （槽名对不上会把 `{brand}` 原样印出来，不报错）。
import type { Task } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Overview from './Overview.vue'

import { listProjectsForTask } from '@/api'
import i18n, { setLocale } from '@/i18n'

vi.mock('@/api', async (orig) => ({
  ...(await orig<typeof import('@/api')>()),
  listProjectsForTask: vi.fn(),
  getResourceLimits: vi.fn().mockResolvedValue({ max_concurrent_turns: 2, max_machines_per_team: 3 }),
}))
vi.mock('@/services/account', async () => {
  const { ref } = await import('vue')
  return { default: { loggedIn: true, user: { id: 1 } }, currentUserId: ref(1) }
})

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const deadline = dayjs().add(10, 'day').valueOf()

/** 一份能把四张卡和三处提示里最关键的分支都点亮的赛题数据；里面不能有汉字，
 *  否则「英文整页无汉字」会栽在夹具上而不是栽在词表上。 */
function taskData(over: Partial<Task> = {}): Task {
  return {
    id: 7,
    name: 'Alpha',
    creator: { id: 1 },
    approved: 'DISAPPROVED',
    rejectReason: 'The description is too short.',
    submitterType: 'TEAM',
    description: '',
    videoUrl: null,
    rank: 2,
    resubmittable: true,
    participantLimit: 5,
    defaultDeadline: 7,
    minTeamSize: 5,
    maxTeamSize: 3,
    deadline,
    joined: false,
    space: { id: 3, name: 'Beta' },
    participationEligibility: {
      user: { eligible: false, reasons: [] },
      teams: [
        {
          team: {
            id: 11,
            name: 'Team A',
            avatarId: null,
            memberRealNameStatus: [
              { memberId: 2, userName: 'Bob', hasRealNameInfo: false },
              { memberId: 3, userName: 'Carol', hasRealNameInfo: true },
            ],
          },
          eligibility: {
            eligible: false,
            reasons: [
              { code: 'TEAM_SIZE_MIN_NOT_MET', message: 'Too few members' },
              { code: 'TEAM_MEMBER_MISSING_REAL_NAME', message: 'A member is not verified' },
            ],
          },
        },
      ],
    },
    ...over,
  } as unknown as Task
}

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.mocked(listProjectsForTask).mockResolvedValue({ data: [] } as unknown as Awaited<
    ReturnType<typeof listProjectsForTask>
  >)
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

function mountPage(data: Task = taskData()) {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Overview },
      { path: '/settings/realname', name: 'UserSettingsRealName', component: stub },
      { path: '/teams/mine', name: 'HomeTeamsMine', component: stub },
      { path: '/teams/:teamId/members', name: 'TeamsDetailMembers', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId/ai', name: 'TasksAIAdvice', component: stub },
    ],
  })
  return render(Overview, {
    props: { taskData: data, participationInfo: null },
    global: { plugins: [router, i18n, createVuetify({ components, directives })] },
  })
}

describe('tasks/detail/Overview', () => {
  it('中文下四张卡和驳回理由都在', async () => {
    setLocale('zh-CN')
    const view = mountPage()

    await view.findByText('赛题详情')
    await view.findByText('赛题信息')
    await view.findByText('时间信息')
    await view.findByText('项目')
    await view.findByText('驳回理由：')
    await view.findByText('The description is too short.')
    expect(view.getByText('小队任务')).toBeTruthy()
    expect(view.getByText('5 队')).toBeTruthy()
    await view.findByText('暂无赛题详情')
  })

  it('英文下整页没有汉字', async () => {
    setLocale('en')
    const view = mountPage()

    await view.findByText('Challenge details')
    await view.findByText('Challenge info')
    await view.findByText('Timing')
    await view.findByText('Projects')
    // 项目卡里嵌着资源限制那条共用组件，它自己也抽过了，正好一起量。
    await view.findByText(
      'Cloud machines: a team shares 3 by default. Your actual quota and usage are on the team compute page.'
    )
    expect(view.container.textContent).not.toContain('{brand}')

    const text = view.container.textContent ?? ''
    expect(CJK.test(text)).toBe(false)
  })

  it('英文下小队不满足条件的三条提示都翻到了', async () => {
    setLocale('en')
    const view = mountPage()

    await view.findByText('No eligible team')
    // 只有一个小队，正好把两形态里的第一条（管「正好 1」）验掉。
    await view.findByText("You have 1 team, but it can't take part in this challenge")
    // 详情是折叠着的，展开之后才画出来。
    const title = await view.findByText('Team A')
    await fireEvent.click(title.closest('button') as Element)
    // 名单里只有没实名的那个人（Carol 已实名，不该出现）。
    await view.findByText('Members not yet verified:')
    expect(view.getByText('Bob')).toBeTruthy()
    expect(view.queryByText('Carol')).toBeNull()
    await view.findByText("This challenge's minimum team size is 5 — invite more people to your team.")
  })

  it('英文下个人任务的提示翻到了', async () => {
    setLocale('en')
    const view = mountPage(
      taskData({
        approved: 'APPROVED',
        submitterType: 'USER',
        participationEligibility: {
          user: { eligible: false, reasons: [{ code: 'USER_MISSING_REAL_NAME', message: 'Real name required' }] },
          teams: [],
        },
      } as Partial<Task>)
    )

    await view.findByText("You can't join yet")
    await view.findByText('This challenge asks for real-name details before you can join.')
    await view.findByText('Fill them in')
    expect(view.getByText('Individual challenge')).toBeTruthy()
  })

  it('英文下品牌名占位符换成了带强调色的品牌名', async () => {
    setLocale('en')
    const view = mountPage()

    const brand = await view.findByText('Navigator AI')
    expect(brand.className).toContain('text-primary')
  })

  it('取回项目列表失败时给的是英文提示', async () => {
    setLocale('en')
    vi.mocked(listProjectsForTask).mockRejectedValue(new Error('offline'))
    const view = mountPage()

    await view.findByText("Couldn't load the project list, so we can't tell whether a project already exists.")
    expect(view.container.textContent ?? '').not.toContain('暂无')
  })
})
