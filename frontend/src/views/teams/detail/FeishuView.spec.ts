/**
 * 团队设置「飞书应用」：能改团队设置的人填应用，Secret 只写不回显。
 *
 * 钉住两件事：保存交出去的是表单上的那三项；已经设过时 Secret 空着保存（表示不改），
 * 而保存成功后那一格清空，不把刚填的 Secret 留在页面上。
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

function mount(props: Record<string, unknown> = {}) {
  return render(FeishuView as unknown as Component, {
    props: { personal: false, app: APP, loading: false, loadError: '', canManage: true, ...props },
    global: { plugins: [createVuetify({ components, directives })], stubs: { ConfirmDialog: true } },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('团队的飞书应用', () => {
  it('保存交出表单上的三项，成功后 Secret 那一格清空', async () => {
    const { emitted } = mount()
    expect(screen.getByText('已设置 · cli_team · feishu')).toBeTruthy()
    const secret = screen.getByLabelText('App Secret') as HTMLInputElement
    await fireEvent.update(secret, 'new-secret')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const [[body, done]] = emitted().save as [[Record<string, string>, (ok: boolean) => void]]
    expect(body).toEqual({ domain: 'feishu', app_id: 'cli_team', app_secret: 'new-secret' })
    done(true)
    await vi_tick()
    expect(secret.value).toBe('')
  })

  it('不是管理员时只说谁能设，不画表单和步骤', () => {
    mount({ canManage: false })
    expect(screen.getByText('只有团队管理员能设置飞书应用。')).toBeTruthy()
    expect(screen.queryByLabelText('App Secret')).toBeNull()
    expect(screen.queryByText('在飞书里建这个应用')).toBeNull()
  })
})

async function vi_tick() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}
