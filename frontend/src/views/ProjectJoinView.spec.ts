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
  preview.mockReset().mockResolvedValue({ project_id: 'p1', project_name: 'Research', already_member: false })
  join.mockReset().mockResolvedValue({ project_id: 'p1' })
})

describe('joining from a shared project link', () => {
  it('waits for consent before granting access', async () => {
    mount()
    await screen.findByText('Research')
    expect(join).not.toHaveBeenCalled()
    await fireEvent.click(screen.getByRole('button', { name: '确认加入' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'workspace-project', params: { projectId: 'p1' } }))
    expect(join).toHaveBeenCalledWith('shared-link')
    expect(refreshProjects).toHaveBeenCalledOnce()
  })

  it('preserves the invitation destination through sign-in', async () => {
    token = ''
    mount()
    await fireEvent.click(screen.getByRole('button', { name: '登录并继续' }))
    expect(push).toHaveBeenCalledWith({ name: 'SignIn', query: { redirect: '/project-invites/shared-link' } })
    expect(preview).not.toHaveBeenCalled()
    expect(join).not.toHaveBeenCalled()
  })

  it('shows an expired link without offering to join', async () => {
    preview.mockRejectedValue(new ApiError(404, 'expired'))
    mount()
    await screen.findByText('邀请链接已失效，请联系项目负责人获取新链接')
    expect(screen.queryByRole('button', { name: '确认加入' })).toBeNull()
    expect(join).not.toHaveBeenCalled()
  })

  it('removes consent when the manager revoked the link after preview', async () => {
    join.mockRejectedValue(new ApiError(404, 'revoked'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '确认加入' }))
    await screen.findByText('邀请链接已失效，请联系项目负责人获取新链接')
    expect(screen.queryByRole('button', { name: '确认加入' })).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })

  it('offers existing members entry without claiming new membership', async () => {
    preview.mockResolvedValue({ project_id: 'p1', project_name: 'Research', already_member: true })
    mount()
    await screen.findByText('你已是项目成员')
    expect(screen.getByRole('button', { name: '进入项目' })).toBeTruthy()
  })
})
