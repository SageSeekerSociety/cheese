/**
 * 团队设置「飞书应用」：每个成员都看得到团队用的应用、在这里连接自己的飞书账号；能改团队
 * 设置的人另外填应用，Secret 只写不回显。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import FeishuView from './FeishuView.vue'

import { setLocale } from '@/i18n'

const APP = {
  configured: true,
  app_id: 'cli_team',
  domain: 'feishu',
  updated_by: 'lead',
  updated_at: null,
  redirect_uri: 'https://okcheese.com/api/integrations/feishu/callback',
}
const CONNECTED = { id: 'i-1', status: 'ok', user_authorized: true }

function mount(props: Record<string, unknown> = {}) {
  return render(FeishuView as unknown as Component, {
    props: {
      personal: false,
      app: APP,
      loading: false,
      loadError: '',
      canManage: true,
      connection: null,
      connecting: false,
      ...props,
    },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        ConfirmDialog: {
          props: ['modelValue', 'title', 'confirmLabel'],
          emits: ['confirm'],
          template:
            '<div v-if="modelValue" role="dialog"><slot /><button @click="$emit(\'confirm\')">{{ confirmLabel }}</button></div>',
        },
      },
    },
  })
}

async function tick() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('团队的飞书应用', () => {
  it('管理员保存交出表单上的三项，成功后 Secret 那一格清空', async () => {
    const { emitted } = mount()
    const secret = screen.getByLabelText('App Secret') as HTMLInputElement
    await fireEvent.update(secret, 'new-secret')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const [[body, done]] = emitted().save as [[Record<string, string>, (ok: boolean) => void]]
    expect(body).toEqual({ domain: 'feishu', app_id: 'cli_team', app_secret: 'new-secret' })
    done(true)
    await tick()
    expect(secret.value).toBe('')
  })

  it('成员看得到团队用的应用、能连接自己的账号，看不到表单和建应用的步骤', async () => {
    const { emitted } = mount({ canManage: false })
    expect(screen.getByText('这个团队用的飞书应用：cli_team（feishu）')).toBeTruthy()
    expect(screen.getByText('还没连接')).toBeTruthy()
    expect(screen.queryByLabelText('App Secret')).toBeNull()
    expect(screen.queryByText('在飞书里建这个应用')).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: '连接我的飞书账号' }))
    expect(emitted().connect).toHaveLength(1)
  })

  it('已连接的成员能重新连接或断开（断开先问一句）', async () => {
    const { emitted } = mount({ canManage: false, connection: CONNECTED })
    expect(screen.getByText('已连接')).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: '重新连接' }))
    expect(emitted().reconnect).toEqual([[CONNECTED]])

    await fireEvent.click(screen.getByRole('button', { name: '断开' }))
    expect(emitted().disconnect).toBeUndefined()
    await fireEvent.click(screen.getAllByRole('button', { name: '断开' }).at(-1)!)
    expect(emitted().disconnect).toEqual([[CONNECTED]])
  })

  it('团队还没设应用时，成员看到谁能设，没有连接按钮', () => {
    mount({ canManage: false, app: { ...APP, configured: false, app_id: '' } })
    expect(screen.getByText('还没设置')).toBeTruthy()
    expect(screen.getByText('团队管理员在这里设置之后，你就能连接自己的飞书账号。')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '连接我的飞书账号' })).toBeNull()
  })
})
