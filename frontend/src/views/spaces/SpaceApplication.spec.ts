// 一份空间申请那一页：读到哪一份、重新提交、撤回或删除之后人落在哪里。
import type { Component } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const applications = vi.fn()
const resubmit = vi.fn()
const del = vi.fn()
vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    applications: (...a: unknown[]) => applications(...a),
    resubmit: (...a: unknown[]) => resubmit(...a),
    del: (...a: unknown[]) => del(...a),
  },
}))
vi.mock('@/api/avatars', () => ({ createAvatar: vi.fn() }))
const confirm = vi.fn()
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: (...a: unknown[]) => ({ wait: () => confirm(...a) }) }),
}))
const { toastSuccess, toastError } = vi.hoisted(() => ({ toastSuccess: vi.fn(), toastError: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: toastSuccess, error: toastError } }))

import i18n, { setLocale } from '@/i18n'
import SpaceApplication from '@/views/spaces/SpaceApplication.vue'

const blank = { template: '<div />' }

function item(reviewStatus: string, extra: Record<string, unknown> = {}) {
  return {
    id: 9,
    name: '量子计算导论',
    intro: '量子比特与量子门',
    avatarId: null,
    reviewStatus,
    description: '',
    owner: 'linxia',
    reviewReason: reviewStatus === 'REJECTED' ? '名称请写明开课学期' : null,
    reviewedBy: null,
    reviewedAt: reviewStatus === 'REJECTED' ? '2026-10-19T03:00:00Z' : null,
    createdAt: '2026-10-18T03:00:00Z',
    ...extra,
  }
}

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: SpaceApplication as Component },
      { path: '/inbox', name: 'inbox', component: blank },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
    ],
  })
  await router.push('/space-applications/9')
  await router.isReady()
  render(
    { template: '<router-view />' },
    { global: { plugins: [router, i18n, createVuetify({ components, directives })] } }
  )
  return router
}

beforeEach(() => {
  setLocale('zh-CN')
  for (const fn of [applications, resubmit, del, confirm, toastSuccess, toastError]) fn.mockReset()
})
afterEach(cleanup)

describe('空间申请', () => {
  it('被驳回：写着原因，改了能重新提交', async () => {
    applications.mockResolvedValue({ items: [item('REJECTED')] })
    resubmit.mockResolvedValue({ application: item('PENDING', { name: '量子计算导论 2026 秋' }) })
    await mount()

    expect(await screen.findByText('名称请写明开课学期')).toBeTruthy()
    await fireEvent.update(screen.getByLabelText('空间名称'), '量子计算导论 2026 秋')
    await fireEvent.click(screen.getByRole('button', { name: '重新提交' }))

    await waitFor(() =>
      expect(resubmit).toHaveBeenCalledWith(9, {
        name: '量子计算导论 2026 秋',
        intro: '量子比特与量子门',
        avatarId: undefined,
      })
    )
    expect(await screen.findByText('待平台管理员审核')).toBeTruthy()
    expect(toastSuccess).toHaveBeenCalledWith('已重新提交')
  })

  it('待审核：撤回要先确认，撤回以后回到待办', async () => {
    applications.mockResolvedValue({ items: [item('PENDING')] })
    confirm.mockResolvedValue(true)
    del.mockResolvedValue(undefined)
    const router = await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '撤回申请' }))
    await waitFor(() => expect(del).toHaveBeenCalledWith(9))
    await waitFor(() => expect(router.currentRoute.value.name).toBe('inbox'))
  })

  it('不确认就不撤回', async () => {
    applications.mockResolvedValue({ items: [item('PENDING')] })
    confirm.mockResolvedValue(false)
    const router = await mount()

    await fireEvent.click(await screen.findByRole('button', { name: '撤回申请' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(del).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('HomeSpaceApplication')
  })

  it('已经过审了：换到那个空间去', async () => {
    applications.mockResolvedValue({ items: [item('APPROVED')] })
    const router = await mount()
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/spaces/9/tasks'))
  })

  it('找不到这份申请：说读不到', async () => {
    applications.mockResolvedValue({ items: [] })
    await mount()
    expect(await screen.findByText('无法读取这份申请')).toBeTruthy()
  })
})
