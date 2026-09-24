import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const preview = vi.fn()
const join = vi.fn()
const push = vi.fn()
const refreshProjects = vi.fn()
let token = 'signed-in'
vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  authToken: () => token,
  previewProjectJoinLink: (...args: unknown[]) => preview(...args),
  joinProjectByLink: (...args: unknown[]) => join(...args),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'shared-link' }, fullPath: '/project-invites/shared-link' }),
  useRouter: () => ({ push }),
}))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ refreshProjects }) }))

import ProjectJoinView from './ProjectJoinView.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'

const direct = { project_id: 'p1', project_name: 'Research', approval: false, join_status: 'none' }
const reviewed = { ...direct, approval: true }

function mount() {
  return render(ProjectJoinView as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  token = 'signed-in'
  push.mockReset()
  refreshProjects.mockReset().mockResolvedValue(undefined)
  preview.mockReset().mockResolvedValue(direct)
  join.mockReset().mockResolvedValue({ ...direct, join_status: 'member' })
})

describe('joining from a shared project link', () => {
  it('waits for consent before granting access', async () => {
    mount()
    await screen.findByText('Research')
    expect(join).not.toHaveBeenCalled()
    await fireEvent.click(screen.getByRole('button', { name: '确认加入' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'workspace-project', params: { projectId: 'p1' } }))
    expect(join).toHaveBeenCalledWith('shared-link', undefined)
    expect(refreshProjects).toHaveBeenCalledOnce()
  })

  it('with approval on, sends the request with its reason and waits instead of entering', async () => {
    preview.mockResolvedValue(reviewed)
    join.mockResolvedValue({ ...reviewed, join_status: 'pending' })
    mount()
    await screen.findByText('加入这个项目需要项目负责人审批。批准后，你将成为普通成员，可以访问项目内全部话题')
    await fireEvent.update(screen.getByLabelText('申请理由（选填）'), '  想一起做实验 ')
    await fireEvent.click(screen.getByRole('button', { name: '申请加入' }))
    await screen.findByText('已提交申请，等待项目负责人审批')
    expect(join).toHaveBeenCalledWith('shared-link', '想一起做实验')
    expect(push).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '申请加入' })).toBeNull()
  })

  it('reopening the link while the request is pending shows the wait, not another request', async () => {
    preview.mockResolvedValue({ ...reviewed, join_status: 'pending' })
    mount()
    await screen.findByText('已提交申请，等待项目负责人审批')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('preserves the invitation destination through sign-in', async () => {
    token = ''
    mount()
    await fireEvent.click(screen.getByRole('button', { name: '登录并继续' }))
    expect(push).toHaveBeenCalledWith({ name: 'SignIn', query: { redirect: '/project-invites/shared-link' } })
    expect(preview).not.toHaveBeenCalled()
    expect(join).not.toHaveBeenCalled()
  })

  it('shows a reset link without offering to join', async () => {
    preview.mockRejectedValue(new ApiError(404, 'reset'))
    mount()
    await screen.findByText('邀请链接已失效，请联系项目负责人获取新链接')
    expect(screen.queryByRole('button', { name: '确认加入' })).toBeNull()
    expect(join).not.toHaveBeenCalled()
  })

  it('removes consent when the manager reset the link after preview', async () => {
    join.mockRejectedValue(new ApiError(404, 'reset'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '确认加入' }))
    await screen.findByText('邀请链接已失效，请联系项目负责人获取新链接')
    expect(screen.queryByRole('button', { name: '确认加入' })).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })

  it('offers existing members entry without claiming new membership', async () => {
    preview.mockResolvedValue({ ...reviewed, join_status: 'member' })
    mount()
    await screen.findByText('你已是项目成员')
    await fireEvent.click(screen.getByRole('button', { name: '进入项目' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'workspace-project', params: { projectId: 'p1' } }))
    expect(join).not.toHaveBeenCalled()
  })
})
