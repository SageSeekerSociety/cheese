// 这一页有个人/小队两套分支，还有两个把对象名字嵌进句子里的对话框。
// 两个对话框是 teleport 到 body 的，render() 给的查询绑在容器上，量它们得换 body；
// 句子里的名字被 <span> 切开，testing-library 只拼直接子文本节点，
// 所以这类「整句」用 textContent 断言，不按元素找。
import type { Task } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getParticipants: vi.fn(),
  updateParticipant: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: mocks }))
vi.mock('vuetify-sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }))

import Participants from './Participants.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const DAY = 86400000

/** 一个已通过审核、带截止时间的小队：它才能点亮「设置截止时间 / 查看提交」。 */
function approvedTeam() {
  return {
    id: 2,
    member: { id: 3, name: 'Team B', avatarId: null },
    createdAt: 1,
    updatedAt: 1,
    deadline: Date.now() + 7 * DAY,
    approved: 'APPROVED',
    applyReason: 'We have done this before.',
    personalAdvantage: 'Two national prizes.',
  }
}

/** 一个待审核的小队：它才有点「通过 / 驳回」的地方，也是驳回对话框的对象。 */
function pendingTeam() {
  return {
    id: 1,
    member: { id: 2, name: 'Team A', avatarId: null },
    createdAt: 1,
    updatedAt: 1,
    deadline: null,
    approved: 'NONE',
    applyReason: 'We build robots.',
    personalAdvantage: 'Ten years of experience.',
    teamMembers: [
      { id: 4, name: 'Bob', intro: '', avatarId: null, isLeader: true },
      { id: 5, name: 'Carol', intro: '', avatarId: null, isLeader: false },
    ],
  }
}

/** 个人报名者：年级/班级和个人优势只在个人分支里画。 */
function pendingIndividual() {
  return {
    id: 6,
    member: { id: 7, name: 'Delta', avatarId: null },
    createdAt: 1,
    updatedAt: 1,
    deadline: null,
    approved: 'NONE',
    realNameInfo: { realName: 'Delta', studentId: '2023001', grade: '2023', className: '3' },
    applyReason: 'I like puzzles.',
    personalAdvantage: 'Wrote a solver.',
  }
}

function taskData(over: Partial<Task> = {}): Task {
  return {
    id: 7,
    name: 'Alpha',
    creator: { id: 1 },
    approved: 'APPROVED',
    submitterType: 'TEAM',
    requireRealName: false,
    description: '',
    rank: 1,
    resubmittable: true,
    participantLimit: 20,
    defaultDeadline: 7,
    deadline: Date.now() + 30 * DAY,
    joined: false,
    ...over,
  } as unknown as Task
}

function mountPage(participants: unknown[], data: Task = taskData()) {
  mocks.getParticipants.mockResolvedValue({ data: { participants } })
  return render(Participants, {
    props: { taskData: data },
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { TaskSubmissionHistory: { template: '<div />' } },
    },
  })
}

/** 某一条报名者那一行的文字。筛选项和状态标签用的是同一个词，按行取才分得清。 */
async function rowText(view: ReturnType<typeof mountPage>, name: string) {
  const row = (await view.findByText(name)).closest('.participant-item')
  if (!row) throw new Error(`no participant row for ${name}`)
  return row.textContent ?? ''
}

beforeEach(() => {
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.clearAllMocks()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('tasks/detail/Participants', () => {
  it('中文下卡片、筛选、报名理由和小队名单都在', async () => {
    setLocale('zh-CN')
    const view = mountPage([pendingTeam(), approvedTeam()])

    await view.findByText('参与者管理')
    // 四个筛选按钮各自带一个数字角标，按名字匹配比按文本匹配稳。
    expect(view.getByRole('button', { name: /全部/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /待审核/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /已通过/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /已驳回/ })).toBeTruthy()
    expect(await rowText(view, 'Team A')).toContain('待审核')
    expect(await rowText(view, 'Team B')).toContain('已通过')
    // 两条报名者各挂一个「团队」标签
    expect(view.getAllByText('团队').length).toBe(2)
    await view.findByText('申请理由：We build robots.')
    await view.findByText('团队优势：Ten years of experience.')
    await view.findByText('团队成员：')
    expect(view.getByText('Bob')).toBeTruthy()
    expect(view.getByText('Carol')).toBeTruthy()
    await view.findByText(/截止时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}/)
    expect(view.getByText('设置截止时间')).toBeTruthy()
    expect(view.getByText('查看提交')).toBeTruthy()
    expect(view.getByText('通过')).toBeTruthy()
    expect(view.getByText('驳回')).toBeTruthy()
  })

  it('英文下整页没有汉字', async () => {
    setLocale('en')
    const view = mountPage([pendingTeam(), approvedTeam()])

    await view.findByText('Participants')
    expect(view.getByRole('button', { name: /All/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /Pending review/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /Approved/ })).toBeTruthy()
    expect(view.getByRole('button', { name: /Rejected/ })).toBeTruthy()
    expect(await rowText(view, 'Team A')).toContain('Pending review')
    expect(await rowText(view, 'Team B')).toContain('Approved')
    await view.findByText('Reason for applying: We build robots.')
    await view.findByText('Team strengths: Ten years of experience.')
    await view.findByText('Team members:')
    await view.findByText(/Deadline: \d{4}-\d{2}-\d{2} \d{2}:\d{2}/)
    expect(view.getByText('Set deadline')).toBeTruthy()
    expect(view.getByText('View submissions')).toBeTruthy()
    expect(view.getByText('Approve')).toBeTruthy()
    expect(view.getByText('Reject')).toBeTruthy()

    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下个人报名者的年级班级和优势翻到了', async () => {
    setLocale('en')
    const view = mountPage([pendingIndividual()], taskData({ submitterType: 'USER', requireRealName: true }))

    await view.findByText('Grade 2023, class 3')
    await view.findByText('Reason for applying: I like puzzles.')
    await view.findByText('Personal strengths: Wrote a solver.')
    await view.findByText('Delta (2023001)')
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下截止时间对话框把小队名字插进句子里', async () => {
    setLocale('en')
    const view = mountPage([approvedTeam()])

    await view.findByText('Participants')
    await fireEvent.click(view.getByRole('button', { name: 'Set deadline' }))

    await waitFor(() => expect(document.body.textContent).toContain('Set a submission deadline for Team B'))
    expect(document.body.textContent ?? '').not.toContain('{name}')
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('英文下驳回对话框把小队名字插进句子里', async () => {
    setLocale('en')
    const view = mountPage([pendingTeam()])

    await view.findByText('Participants')
    await fireEvent.click(view.getByRole('button', { name: 'Reject' }))

    await waitFor(() => expect(document.body.textContent).toContain('You are rejecting the application from Team A'))
    expect(document.body.textContent ?? '').not.toContain('{name}')
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })

  it('取回名单失败时给的是英文提示且没有假数据', async () => {
    setLocale('en')
    mocks.getParticipants.mockRejectedValue(new Error('offline'))
    const view = render(Participants, {
      props: { taskData: taskData() },
      global: {
        plugins: [createVuetify({ components, directives }), i18n],
        stubs: { TaskSubmissionHistory: { template: '<div />' } },
      },
    })

    await view.findByText('No participants yet')
    await view.findByText('Nobody has applied to this challenge yet')
    expect(mocks.error).toHaveBeenCalledWith("Couldn't load the participants")
    expect(CJK.test(document.body.textContent ?? '')).toBe(false)
  })
})
