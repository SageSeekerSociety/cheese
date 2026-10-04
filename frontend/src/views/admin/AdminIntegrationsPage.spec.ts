/**
 * 后台的「飞书应用」：管理员填一次 App ID / Secret / 域名。
 *
 * 四条，每条都钉一个会静默坏掉的地方：
 *
 * 1. **没配过时说的是「还没配置」**，而不是画一个空表单让人以为填过了 —— 成员那边的
 *    按钮灰不灰，读的就是这句话的答案。
 * 2. **Secret 只写不回显**：读回来的结构里没有它，所以输入框永远空着；只改 App ID 或
 *    域名再保存时，请求里 `app_secret` 是空串（服务端把它理解成「不改已经存下的那一
 *    个」）。这一条要是坏了，症状是「改个域名，应用就再也连不上了」。
 * 3. **保存失败说服务端原话，表单里的字一个不动**；App ID 空着时根本不发那一次请求。
 * 4. **读失败时不说「还没配置」，也不画表单** —— 那个答案只有读回来才知道，重试是
 *    页面上唯一能做的事。首屏还没读回来时同理，先不回答。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getPlatformFeishuApp = vi.fn()
const savePlatformFeishuApp = vi.fn()

vi.mock('@/api/feishu', async () => ({
  ...(await vi.importActual<typeof import('@/api/feishu')>('@/api/feishu')),
  getPlatformFeishuApp: () => getPlatformFeishuApp(),
  savePlatformFeishuApp: (body: unknown) => savePlatformFeishuApp(body),
}))

import AdminIntegrationsPage from './AdminIntegrationsPage.vue'

import i18n, { setLocale } from '@/i18n'

const UNCONFIGURED = { configured: false, app_id: '', domain: 'feishu', updated_by: '', updated_at: null }
const CONFIGURED = {
  configured: true,
  app_id: 'cli_platform',
  domain: 'feishu',
  updated_by: 'andy',
  updated_at: '2026-09-29T02:00:00Z',
}

function mount() {
  const vuetify = createVuetify({ components, directives })
  return render(AdminIntegrationsPage as unknown as Component, {
    global: { plugins: [vuetify, i18n] },
  })
}

const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement

beforeEach(() => {
  setLocale('zh-CN')
  getPlatformFeishuApp.mockReset().mockResolvedValue(UNCONFIGURED)
  savePlatformFeishuApp.mockReset()
})

afterEach(cleanup)

describe('还没配过', () => {
  it('说的是「还没配置」，填两格保存之后就变成已配置', async () => {
    await mount()
    await screen.findByText('还没配置')

    await fireEvent.update(field('App ID'), 'cli_platform')
    await fireEvent.update(field('App Secret'), 'the-secret')

    savePlatformFeishuApp.mockResolvedValue(CONFIGURED)
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await vi.waitFor(() =>
      expect(savePlatformFeishuApp).toHaveBeenCalledWith({
        app_id: 'cli_platform',
        app_secret: 'the-secret',
        domain: 'feishu',
      })
    )
    expect(await screen.findByText(/已配置 · cli_platform/)).toBeTruthy()
  })

  it('App ID 空着就不发那一次请求', async () => {
    await mount()
    await screen.findByText('还没配置')

    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('先填 App ID')).toBeTruthy()
    expect(savePlatformFeishuApp).not.toHaveBeenCalled()
  })

  it('首屏还没读回来时先不说「还没配置」', async () => {
    let settle: (value: typeof UNCONFIGURED) => void = () => {}
    getPlatformFeishuApp.mockReturnValue(new Promise((resolve) => (settle = resolve)))
    await mount()

    expect(screen.queryByText('还没配置')).toBeNull()

    settle(UNCONFIGURED)
    expect(await screen.findByText('还没配置')).toBeTruthy()
  })
})

describe('已经配过的应用', () => {
  it('Secret 是空的；不改它的时候保存带的是空串', async () => {
    getPlatformFeishuApp.mockResolvedValue(CONFIGURED)
    savePlatformFeishuApp.mockResolvedValue({ ...CONFIGURED, app_id: 'cli_renamed' })
    await mount()

    expect(await screen.findByText(/已配置 · cli_platform/)).toBeTruthy()
    expect(screen.getByText(/最后由 andy 更新/)).toBeTruthy()
    expect(field('App Secret').value).toBe('')

    await fireEvent.update(field('App ID'), 'cli_renamed')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 空串 = 「不改已经存下的那一个」：服务端读回来的结构里没有 Secret，
    // 所以「页面没动它」和「要把它清掉」在请求里长得一样，而后者不是这个页面能表达的事。
    await vi.waitFor(() =>
      expect(savePlatformFeishuApp).toHaveBeenCalledWith({
        app_id: 'cli_renamed',
        app_secret: '',
        domain: 'feishu',
      })
    )
  })

  it('保存被拒时说服务端那句话，填的东西还在', async () => {
    getPlatformFeishuApp.mockResolvedValue(CONFIGURED)
    savePlatformFeishuApp.mockRejectedValue(new Error('App Secret 不能为空'))
    await mount()

    await screen.findByText(/已配置 · cli_platform/)
    await fireEvent.update(field('App ID'), 'cli_renamed')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    // 这块表单还在屏幕上，失败就地说一声，服务端原话接在冒号后（§8.9、§3.11）。
    expect(await screen.findByText('保存失败：App Secret 不能为空')).toBeTruthy()
    expect(field('App ID').value).toBe('cli_renamed')
  })
})

describe('这一页读不到', () => {
  it('说的是读不到加重试，不说「还没配置」，也不画那张表单', async () => {
    getPlatformFeishuApp.mockRejectedValue(new Error('boom'))
    await mount()

    expect(await screen.findByRole('button', { name: '重试' })).toBeTruthy()
    expect(screen.queryByText('还没配置')).toBeNull()
    expect(screen.queryByLabelText('App ID')).toBeNull()
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
  })

  it('点重试是再读一次，读回来就画正常那一页', async () => {
    getPlatformFeishuApp.mockRejectedValueOnce(new Error('boom'))
    await mount()
    await screen.findByRole('button', { name: '重试' })

    getPlatformFeishuApp.mockResolvedValue(CONFIGURED)
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText(/已配置 · cli_platform/)).toBeTruthy()
    expect(getPlatformFeishuApp).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })
})
