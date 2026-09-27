// 「团队」页上的「创建团队」对话框。
//
// 团队名全站唯一，撞名时后端回 409 + data.field=name。这是用户换个名字就能解决的
// 错，所以它必须落到名字那一格；以前它和别的失败一样只弹「创建团队失败，请稍后
// 重试」，用户照做重试，每次都一样失败。
import type { Component } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const create = vi.fn()
const toastError = vi.fn()
vi.mock('@/network/api/teams', () => ({ TeamsApi: { create: (...a: unknown[]) => create(...a) } }))
vi.mock('@/network/api/avatars', () => ({ AvatarsApi: { createAvatar: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn() } }))

import TeamsIndex from './Index.vue'

import i18n, { setLocale, t } from '@/i18n'
import { BusinessError } from '@/network/types/error'

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/teams', name: 'HomeTeamsExplore', component: { template: '<div />' } },
      { path: '/teams/mine', name: 'HomeTeamsMine', component: { template: '<div />' } },
      { path: '/teams/pending', name: 'HomeTeamsPending', component: { template: '<div />' } },
      { path: '/teams/:handle', name: 'TeamsDetailDefault', component: { template: '<div />' } },
    ],
  })
  await router.push('/teams')
  await router.isReady()
  const view = render(TeamsIndex as unknown as Component, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
  return { view, router }
}

async function submit(name: string) {
  await fireEvent.click(screen.getByRole('button', { name: /创建团队/ }))
  const input = await waitFor(() => screen.getByLabelText('团队名称') as HTMLInputElement)
  await fireEvent.update(input, name)
  const buttons = screen.getAllByRole('button', { name: /创建团队/ })
  await fireEvent.click(buttons[buttons.length - 1])
}

beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})

beforeEach(() => {
  setLocale('zh-CN')
  create.mockReset()
  toastError.mockReset()
})
afterEach(() => {
  cleanup()
  // v-dialog teleports its overlay to <body>, outside what cleanup unmounts.
  document.body.innerHTML = ''
})

describe('creating a team', () => {
  it('tells the user on the name field that the name is taken, instead of asking them to retry', async () => {
    create.mockRejectedValueOnce(
      new BusinessError('Team name already exists', 409, {
        name: 'ConflictError',
        message: 'Team name already exists',
        data: { field: 'name', value: '11111' },
      })
    )
    const { router } = await mountPage()
    await submit('11111')

    await waitFor(() => expect(screen.getByText(t('work.teamProfile.nameTaken'))).toBeTruthy())
    expect(toastError).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('HomeTeamsExplore')
  })

  it('opens the new team once it is created', async () => {
    create.mockResolvedValueOnce({ data: { team: { id: 9, handle: 'team-9' } } })
    const { router } = await mountPage()
    await submit('一个新名字')

    await waitFor(() => expect(router.currentRoute.value.params.handle).toBe('team-9'))
  })
})
