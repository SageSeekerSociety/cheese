import type { Component } from 'vue'
import type { Team } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const detail = vi.fn()
const join = vi.fn()
const push = vi.fn()
let token = 'signed-in'
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  authToken: () => token,
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    detailByJoinLink: (...args: unknown[]) => detail(...args),
    joinByJoinLink: (...args: unknown[]) => join(...args),
  },
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'team-link' }, fullPath: '/team-invites/team-link' }),
  useRouter: () => ({ push }),
}))

import TeamInviteView from './TeamInviteView.vue'

import { setLocale } from '@/i18n'
import { BusinessError } from '@/network/types/error'

function team(overrides: Partial<Team> = {}): Team {
  return {
    id: 7,
    handle: 'cheese-core',
    name: 'Cheese 核心组',
    intro: '做 Cheese 的人',
    avatarId: 1,
    owner: { id: 1, nickname: '芝士' } as Team['owner'],
    admins: { total: 1, examples: [] },
    members: { total: 3, examples: [] },
    visibility: 'stealth',
    joinStatus: 'none',
    joinApproval: true,
    ...overrides,
  }
}

function mount() {
  return render(TeamInviteView as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  token = 'signed-in'
  push.mockReset()
  detail.mockReset().mockResolvedValue({ data: { team: team() } })
  join.mockReset().mockResolvedValue({ data: { team: team({ joinStatus: 'pending' }) } })
})
afterEach(cleanup)

describe('opening a team link', () => {
  it('shows who the team is before anyone asks to join', async () => {
    mount()
    await screen.findByText('Cheese 核心组')
    screen.getByText('做 Cheese 的人')
    screen.getByText(/所有者：芝士/)
    screen.getByText(/5 名成员/)
    expect(detail).toHaveBeenCalledWith('team-link')
    expect(join).not.toHaveBeenCalled()
  })

  it('sends a request with a reason when the team approves joins, then says it is waiting', async () => {
    mount()
    await screen.findByText('加入这个团队需要团队所有者或管理员批准')
    await fireEvent.update(screen.getByLabelText('申请理由（选填）'), '  我是新来的  ')
    await fireEvent.click(screen.getByRole('button', { name: '申请加入' }))
    await screen.findByText('已提交申请，等待团队管理员审批')
    expect(join).toHaveBeenCalledWith('team-link', { message: '我是新来的' })
    expect(screen.queryByRole('button', { name: '申请加入' })).toBeNull()
  })

  it('joins directly when the team does not require approval', async () => {
    detail.mockResolvedValue({ data: { team: team({ joinApproval: false }) } })
    join.mockResolvedValue({ data: { team: team({ joinApproval: false, joinStatus: 'member' }) } })
    mount()
    await screen.findByText('确认后你将成为团队成员，可以使用团队的项目和算力')
    expect(screen.queryByLabelText('申请理由（选填）')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '加入团队' }))
    await screen.findByText('你已经在这个团队里')
    expect(join).toHaveBeenCalledWith('team-link', { message: undefined })
    screen.getByText('进入团队')
  })

  it('does not offer to apply again while a request is pending', async () => {
    detail.mockResolvedValue({ data: { team: team({ joinStatus: 'pending' }) } })
    mount()
    await screen.findByText('已提交申请，等待团队管理员审批')
    expect(screen.queryByRole('button', { name: '申请加入' })).toBeNull()
  })

  it('offers members a way in instead of a join button', async () => {
    detail.mockResolvedValue({ data: { team: team({ joinStatus: 'member' }) } })
    mount()
    await screen.findByText('你已经在这个团队里')
    expect(screen.queryByRole('button', { name: '申请加入' })).toBeNull()
  })

  it('says the link no longer works after it was reset', async () => {
    detail.mockRejectedValue(new BusinessError('not found', 404))
    mount()
    await screen.findByText('团队链接已失效，请向团队管理员索取新链接')
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('keeps the link through sign-in', async () => {
    token = ''
    mount()
    await fireEvent.click(screen.getByRole('button', { name: '登录并继续' }))
    expect(push).toHaveBeenCalledWith({ name: 'SignIn', query: { redirect: '/team-invites/team-link' } })
    expect(detail).not.toHaveBeenCalled()
  })

  it('keeps the form when the request fails', async () => {
    join.mockRejectedValue(new BusinessError('boom', 500))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '申请加入' }))
    await screen.findByText('操作失败，请重试')
    await waitFor(() => screen.getByRole('button', { name: '申请加入' }))
  })
})
