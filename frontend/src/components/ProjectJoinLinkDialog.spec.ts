import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

const current = vi.fn()
const approval = vi.fn()
const reset = vi.fn()
const copy = vi.fn()
vi.mock('@/api', () => ({
  getProjectJoinLink: (...args: unknown[]) => current(...args),
  setProjectJoinApproval: (...args: unknown[]) => approval(...args),
  resetProjectJoinLink: (...args: unknown[]) => reset(...args),
}))

import ProjectJoinLinkDialog from './ProjectJoinLinkDialog.vue'

import { setLocale } from '@/i18n'

const link = { token: 'share-token', approval: true }

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
  current.mockReset().mockResolvedValue(link)
  approval
    .mockReset()
    .mockImplementation((_pid: string, value: boolean) => Promise.resolve({ ...link, approval: value }))
  reset.mockReset().mockResolvedValue({ token: 'fresh-token', approval: true })
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

it('shows the permanent link and copies it', async () => {
  await open()
  await fireEvent.click(await screen.findByRole('button', { name: '复制链接' }))
  expect(current).toHaveBeenCalledWith('p1')
  expect(copy).toHaveBeenCalledWith(`${location.origin}/project-invites/share-token`)
  await screen.findByRole('button', { name: '已复制' })
  expect(screen.queryByText(/有效期/)).toBeNull()
})

it('turns approval off and says joining no longer waits for anyone', async () => {
  await open()
  await screen.findByText('对方提交申请，由项目负责人批准后才加入')
  // 原生 click()：jsdom 只在默认行为里补发 change，而 Vuetify 听的是 change。
  ;(screen.getByRole('checkbox', { name: '加入需要审批' }) as HTMLInputElement).click()
  await waitFor(() => expect(approval).toHaveBeenCalledWith('p1', false))
  await screen.findByText('对方确认后直接加入，不经过审批')
})

it('resets only after a second, explicit confirmation and shows the new link', async () => {
  await open()
  await fireEvent.click(await screen.findByRole('button', { name: '重置链接' }))
  expect(reset).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button', { name: '确认重置' }))
  await waitFor(() => expect(reset).toHaveBeenCalledWith('p1'))
  await waitFor(() =>
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toContain('/project-invites/fresh-token')
  )
})

it('keeps the link readable when clipboard access fails', async () => {
  copy.mockRejectedValue(new Error('clipboard unavailable'))
  await open()
  await fireEvent.click(await screen.findByRole('button', { name: '复制链接' }))
  await screen.findByText('复制失败，请选择链接后手动复制')
  expect(screen.queryByRole('button', { name: '已复制' })).toBeNull()
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toContain('/project-invites/share-token')
})
