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
const createAvatar = vi.fn()
const toastError = vi.fn()
vi.mock('@/api/teams', () => ({ TeamsApi: { create: (...a: unknown[]) => create(...a) } }))
vi.mock('@/api/avatars', () => ({ createAvatar: (...a: unknown[]) => createAvatar(...a) }))
vi.mock('vuetify-sonner', () => ({ toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn() } }))

import TeamsIndex from './Index.vue'

import { ApiError } from '@/api'
import i18n, { setLocale, t } from '@/i18n'

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

async function submit(name: string, avatar?: File) {
  await fireEvent.click(screen.getByRole('button', { name: /创建团队/ }))
  const input = await waitFor(() => screen.getByLabelText('团队名称') as HTMLInputElement)
  await fireEvent.update(input, name)
  if (avatar) {
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [avatar] } })
  }
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
  createAvatar.mockReset()
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
      new ApiError(409, 'Team name already exists', 'ConflictError', undefined, undefined, {
        field: 'name',
        value: '11111',
      })
    )
    const { router } = await mountPage()
    await submit('11111')

    await waitFor(() => expect(screen.getByText(t('work.teamProfile.nameTaken'))).toBeTruthy())
    expect(toastError).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('HomeTeamsExplore')
  })

  it('opens the new team once it is created', async () => {
    create.mockResolvedValueOnce({ team: { id: 9, handle: 'team-9' } })
    const { router } = await mountPage()
    await submit('一个新名字')

    await waitFor(() => expect(router.currentRoute.value.params.handle).toBe('team-9'))
  })

  // 服务端说了原因，人却只看到「创建团队失败，请稍后重试」，他会照着重试，每次都
  // 一样失败。现在两行都在：上一行还是那句「稍后重试」，下一行是服务端自己那句话。
  it('keeps the retry line and adds what the server said under it', async () => {
    create.mockRejectedValueOnce(
      new ApiError(503, '内容安全审核暂时不可用，这次没有保存，稍后重试', 'UpstreamUnavailable')
    )
    await mountPage()
    await submit('一个新名字')

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('创建团队失败，请稍后重试', {
        description: '内容安全审核暂时不可用，这次没有保存，稍后重试',
      })
    )
  })

  // 服务端没给原因（网络断了）时只有一行，不凑一个空的第二行。
  it('says one line when there is no reason from the server', async () => {
    create.mockRejectedValueOnce(new Error('offline'))
    await mountPage()
    await submit('一个新名字')

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('创建团队失败，请稍后重试', undefined))
  })

  // 传图和建团队是两件事。都说「创建团队失败」时，人会去重试同一张图——而那张图
  // 就是没成的原因。
  it('blames the avatar when the avatar is what failed', async () => {
    createAvatar.mockRejectedValueOnce(
      new ApiError(503, '内容安全审核暂时不可用，这次没有保存，稍后重试', 'UpstreamUnavailable')
    )
    await mountPage()
    await submit('一个新名字', new File(['x'], 'avatar.png', { type: 'image/png' }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('上传头像失败，换一张或稍后重试', {
        description: '内容安全审核暂时不可用，这次没有保存，稍后重试',
      })
    )
    expect(create).not.toHaveBeenCalled()
  })
})
