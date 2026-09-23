import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

const current = vi.fn()
const create = vi.fn()
const revoke = vi.fn()
const copy = vi.fn()
vi.mock('@/api', () => ({
  getProjectJoinLink: (...args: unknown[]) => current(...args),
  createProjectJoinLink: (...args: unknown[]) => create(...args),
  revokeProjectJoinLink: (...args: unknown[]) => revoke(...args),
}))

import ProjectJoinLinkDialog from './ProjectJoinLinkDialog.vue'

import { setLocale } from '@/i18n'

const link = { token: 'share-token', expires_at: '2026-09-29T00:00:00Z' }

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
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } })
})

beforeEach(() => {
  setLocale('zh-CN')
  current.mockReset().mockResolvedValue(null)
  create.mockReset().mockResolvedValue(link)
  revoke.mockReset().mockResolvedValue({ deleted: true })
  copy.mockReset().mockResolvedValue(undefined)
})

async function open() {
  const wrapper = render(ProjectJoinLinkDialog as unknown as Component, {
    props: { projectId: 'p1', modelValue: false },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await wrapper.rerender({ modelValue: true })
  return wrapper
}

it('generates only on request, copies the join URL, then revokes it', async () => {
  await open()
  await screen.findByText('暂无有效邀请链接')
  expect(create).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button', { name: '生成链接' }))
  await fireEvent.click(await screen.findByRole('button', { name: '复制链接' }))
  expect(create).toHaveBeenCalledWith('p1')
  expect(copy).toHaveBeenCalledWith(`${location.origin}/project-invites/share-token`)
  await screen.findByRole('button', { name: '已复制' })
  await fireEvent.click(screen.getByRole('button', { name: '撤销链接' }))
  await waitFor(() => expect(revoke).toHaveBeenCalledWith('p1'))
  await screen.findByText('暂无有效邀请链接')
})

it('shows an existing link without changing it', async () => {
  current.mockResolvedValue(link)
  await open()
  await screen.findByRole('button', { name: '复制链接' })
  expect(create).not.toHaveBeenCalled()
})

it('keeps the link readable when clipboard access fails', async () => {
  current.mockResolvedValue(link)
  copy.mockRejectedValue(new Error('clipboard unavailable'))
  await open()
  await fireEvent.click(await screen.findByRole('button', { name: '复制链接' }))
  await screen.findByText('复制失败，请选择链接后手动复制')
  expect(screen.queryByRole('button', { name: '已复制' })).toBeNull()
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toContain('/project-invites/share-token')
})
