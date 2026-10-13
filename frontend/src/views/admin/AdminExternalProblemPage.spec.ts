/**
 * 后台的「外站题账号」：管理员填一个平台读外站题（今天只有 YOJ）成绩用的账号。
 *
 * 四条，每条都钉一个会静默坏掉的地方：
 *
 * 1. **没配过时说的是「还没配置」**，而不是画一个空表单让人以为填过了 —— 那正是平台读不
 *    读得到数的答案。
 * 2. **密码只写不回显**：读回来的结构里没有它，所以输入框永远空着；只改账号再保存时，请求
 *    里 `password` 是空串（服务端把它理解成「不改已经存下的那一个」）。这一条要是坏了，
 *    症状是「改个账号，外站题那边就再也读不到数了」。
 * 3. **保存失败说服务端原话，表单里的字一个不动**；账号空着时根本不发那一次请求。
 * 4. **读失败时不说「还没配置」，也不画表单** —— 那个答案只有读回来才知道，重试是页面上
 *    唯一能做的事。首屏还没读回来时同理，先不回答。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getExternalProblemAccount = vi.fn()
const saveExternalProblemAccount = vi.fn()

vi.mock('@/api/externalProblem', async () => ({
  ...(await vi.importActual<typeof import('@/api/externalProblem')>('@/api/externalProblem')),
  getExternalProblemAccount: () => getExternalProblemAccount(),
  saveExternalProblemAccount: (body: unknown) => saveExternalProblemAccount(body),
}))

import AdminExternalProblemPage from './AdminExternalProblemPage.vue'

import i18n, { setLocale } from '@/i18n'

const UNCONFIGURED = { configured: false, source: 'yoj', username: '', updated_by: '', updated_at: null }
const CONFIGURED = {
  configured: true,
  source: 'yoj',
  username: 'teacher01',
  updated_by: 'andy',
  updated_at: '2026-09-29T02:00:00Z',
}

function mount() {
  const vuetify = createVuetify({ components, directives })
  return render(AdminExternalProblemPage as unknown as Component, {
    global: { plugins: [vuetify, i18n] },
  })
}

const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement

beforeEach(() => {
  setLocale('zh-CN')
  getExternalProblemAccount.mockReset().mockResolvedValue(UNCONFIGURED)
  saveExternalProblemAccount.mockReset()
})

afterEach(cleanup)

describe('还没配过', () => {
  it('说的是「还没配置」，填两格保存之后就变成已配置', async () => {
    await mount()
    await screen.findByText('还没配置')

    await fireEvent.update(field('账号'), 'teacher01')
    await fireEvent.update(field('密码'), 'the-password')

    saveExternalProblemAccount.mockResolvedValue(CONFIGURED)
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await vi.waitFor(() =>
      expect(saveExternalProblemAccount).toHaveBeenCalledWith({
        username: 'teacher01',
        password: 'the-password',
      })
    )
    expect(await screen.findByText(/已配置 · teacher01/)).toBeTruthy()
  })

  it('账号空着就不发那一次请求', async () => {
    await mount()
    await screen.findByText('还没配置')

    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('先填账号')).toBeTruthy()
    expect(saveExternalProblemAccount).not.toHaveBeenCalled()
  })

  it('首屏还没读回来时先不说「还没配置」', async () => {
    let settle: (value: typeof UNCONFIGURED) => void = () => {}
    getExternalProblemAccount.mockReturnValue(new Promise((resolve) => (settle = resolve)))
    await mount()

    expect(screen.queryByText('还没配置')).toBeNull()

    settle(UNCONFIGURED)
    expect(await screen.findByText('还没配置')).toBeTruthy()
  })
})

describe('已经配过的账号', () => {
  it('密码是空的；不改它的时候保存带的是空串', async () => {
    getExternalProblemAccount.mockResolvedValue(CONFIGURED)
    saveExternalProblemAccount.mockResolvedValue({ ...CONFIGURED, username: 'teacher02' })
    await mount()

    expect(await screen.findByText(/已配置 · teacher01/)).toBeTruthy()
    expect(screen.getByText(/最后由 andy 更新/)).toBeTruthy()
    expect(field('密码').value).toBe('')

    await fireEvent.update(field('账号'), 'teacher02')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 空串 = 「不改已经存下的那一个」：服务端读回来的结构里没有密码，
    // 所以「页面没动它」和「要把它清掉」在请求里长得一样，而后者不是这个页面能表达的事。
    await vi.waitFor(() =>
      expect(saveExternalProblemAccount).toHaveBeenCalledWith({
        username: 'teacher02',
        password: '',
      })
    )
  })

  it('保存被拒时说服务端那句话，填的东西还在', async () => {
    getExternalProblemAccount.mockResolvedValue(CONFIGURED)
    saveExternalProblemAccount.mockRejectedValue(new Error('账号或密码不对'))
    await mount()

    await screen.findByText(/已配置 · teacher01/)
    await fireEvent.update(field('账号'), 'teacher02')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('保存失败：账号或密码不对')).toBeTruthy()
    expect(field('账号').value).toBe('teacher02')
  })
})

describe('这一页读不到', () => {
  it('说的是读不到加重试，不说「还没配置」，也不画那张表单', async () => {
    getExternalProblemAccount.mockRejectedValue(new Error('boom'))
    await mount()

    expect(await screen.findByRole('button', { name: '重试' })).toBeTruthy()
    expect(screen.queryByText('还没配置')).toBeNull()
    expect(screen.queryByLabelText('账号')).toBeNull()
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  })

  it('点重试是再读一次，读回来就画正常那一页', async () => {
    getExternalProblemAccount.mockRejectedValueOnce(new Error('boom'))
    await mount()
    await screen.findByRole('button', { name: '重试' })

    getExternalProblemAccount.mockResolvedValue(CONFIGURED)
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText(/已配置 · teacher01/)).toBeTruthy()
    expect(getExternalProblemAccount).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })
})
