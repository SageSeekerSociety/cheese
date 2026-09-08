import { reactive } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Landing from './Landing.vue'

import AccountService from '@/services/account'

vi.mock('@/services/account', () => ({ default: reactive({ loggedIn: false }) }))

afterEach(() => {
  cleanup()
  AccountService.loggedIn = false
})

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Landing },
      { path: '/spaces', name: 'HomeSpaces', component: { template: '<div>空间</div>' } },
    ],
  })
  await router.push('/')
  const view = render(Landing, { global: { plugins: [router], stubs: { VIcon: true } } })
  return { ...view, router }
}

describe('公开首页', () => {
  it('可用键盘走完整个项目演示并打开成果示例', async () => {
    const view = await mount()
    const first = view.getByRole('tab', { name: /01\s*需求讨论/ })
    await fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(view.getByRole('tab', { name: /02\s*协作执行/ }).getAttribute('aria-selected')).toBe('true')
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    await fireEvent.click(view.getByRole('button', { name: '查看回答示例' }))
    expect(view.getByText('来源示例：项目入门指南 §1')).toBeTruthy()
    await fireEvent.click(view.getByRole('tab', { name: /04\s*成果交付/ }))
    await fireEvent.click(view.getByRole('button', { name: '查看成果示例' }))
    expect(view.getByText(/原型展示资料引用位置/)).toBeTruthy()
  })

  it('企业解决方案默认可见，也可切换到高校场景', async () => {
    const view = await mount()
    expect(view.getByRole('heading', { name: '让团队把 AI 用进真实项目。' })).toBeTruthy()
    await fireEvent.click(view.getByRole('tab', { name: '高校与机构' }))
    expect(view.getByRole('heading', { name: '让实践育人，发生在真实项目里。' })).toBeTruthy()
  })

  it('会话恢复成功后进入用户空间', async () => {
    const { router } = await mount()
    AccountService.loggedIn = true
    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaces'))
  })
})
