/**
 * 团队设置「飞书应用」这一栏的容器：存、删了团队的应用之后，本人那张连接卡片说的是服务端
 * 现在的样子。换了 App ID 或删掉应用，服务端已经把本人的连接打回「需要重新授权」，卡片不能
 * 还说已连接。画面由 `FeishuView.vue` 画，这里换成一个只摆出连接状态的替身。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getTeamFeishuApp = vi.fn()
const saveTeamFeishuApp = vi.fn()
const removeTeamFeishuApp = vi.fn()

vi.mock('@/api/feishu', async () => ({
  ...(await vi.importActual<typeof import('@/api/feishu')>('@/api/feishu')),
  getTeamFeishuApp: (id: number) => getTeamFeishuApp(id),
  saveTeamFeishuApp: (id: number, body: unknown) => saveTeamFeishuApp(id, body),
  removeTeamFeishuApp: (id: number) => removeTeamFeishuApp(id),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Feishu from './Feishu.vue'

import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const VIEW = {
  props: ['app', 'connection', 'loadError'],
  emits: ['save', 'remove'],
  template: `<div>
    <p data-testid="app">{{ app?.app_id ?? '' }}</p>
    <p data-testid="mine">{{ connection ? connection.status + '/' + connection.user_authorized : 'none' }}</p>
    <p v-if="loadError" role="alert">{{ loadError }}</p>
    <button @click="$emit('save', { app_id: 'cli_b', app_secret: 's', domain: 'feishu' }, () => {})">save</button>
    <button @click="$emit('remove')">remove</button>
  </div>`,
}

function view(appId: string | null, connection: unknown) {
  return {
    configured: !!appId,
    app_id: appId ?? '',
    domain: 'feishu',
    updated_by: '',
    updated_at: null,
    redirect_uri: 'r',
    can_manage: true,
    connection,
  }
}
const CONNECTED = { id: 'i-1', status: 'ok', user_authorized: true }
const SENT_BACK = { id: 'i-1', status: 'auth_failed', user_authorized: false }

function mount() {
  return render(Feishu as unknown as Component, {
    global: {
      provide: { [teamDataInjectionKey as symbol]: ref({ id: 12, personal: false }) },
      stubs: { FeishuView: VIEW },
    },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  getTeamFeishuApp.mockReset()
  saveTeamFeishuApp.mockReset()
  removeTeamFeishuApp.mockReset()
})
afterEach(cleanup)

describe('存、删了团队的应用之后', () => {
  it('换了 App ID：本人那张卡片重读，说需要重新授权，不再说已连接', async () => {
    getTeamFeishuApp.mockResolvedValueOnce(view('cli_a', CONNECTED)).mockResolvedValue(view('cli_b', SENT_BACK))
    // 写回来的那一份不带本人的连接，卡片要靠重读。
    saveTeamFeishuApp.mockResolvedValue(view('cli_b', null))
    mount()

    expect((await screen.findByText('ok/true')).textContent).toBe('ok/true')
    await fireEvent.click(screen.getByText('save'))
    expect(await screen.findByText('auth_failed/false')).toBeTruthy()
    expect(screen.getByTestId('app').textContent).toBe('cli_b')
  })

  it('删掉应用：卡片重读，不留旧连接说正常', async () => {
    getTeamFeishuApp.mockResolvedValueOnce(view('cli_a', CONNECTED)).mockResolvedValue(view(null, SENT_BACK))
    removeTeamFeishuApp.mockResolvedValue(view(null, null))
    mount()

    expect(await screen.findByText('ok/true')).toBeTruthy()
    await fireEvent.click(screen.getByText('remove'))
    expect(await screen.findByText('auth_failed/false')).toBeTruthy()
  })

  it('重读失败时说读不到，不把旧的连接状态留着当真', async () => {
    getTeamFeishuApp.mockResolvedValueOnce(view('cli_a', CONNECTED)).mockRejectedValue(new Error('网络断了'))
    saveTeamFeishuApp.mockResolvedValue(view('cli_b', null))
    mount()

    expect(await screen.findByText('ok/true')).toBeTruthy()
    await fireEvent.click(screen.getByText('save'))
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})
