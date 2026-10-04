// 「通用」: the switch shows what the app says, and changing it changes it in the
// app; when the system refuses, the switch stays where the truth is.
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { toast } from 'vuetify-sonner'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import General from './General.vue'

import { setLocale } from '@/i18n'

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }

function desktopApp(opensAtLogin: boolean, refuse = false) {
  const invoke = vi.fn(async (cmd: string) => {
    if (cmd === 'opens_at_login') return opensAtLogin
    if (cmd === 'set_opens_at_login' && refuse) throw new Error('not allowed')
    return undefined
  })
  ;(window as AppWindow).__TAURI__ = { core: { invoke } }
  ;(window as AppWindow).__CHEESE_APP__ = { origin: 'https://okcheese.com', can: ['autostart'] }
  return invoke
}

function show() {
  return render(General, { global: { plugins: [createVuetify({ components, directives })] } })
}

beforeEach(() => setLocale('zh-CN'))

afterEach(() => {
  cleanup()
  delete (window as AppWindow).__TAURI__
  delete (window as AppWindow).__CHEESE_APP__
})

describe('General settings', () => {
  it('shows whether the app opens at login, as the app says', async () => {
    desktopApp(true)
    const view = show()
    const control = (await view.findByLabelText('开机时启动')) as HTMLInputElement
    await waitFor(() => expect(control.checked).toBe(true))
  })

  it('turns opening at login on in the app', async () => {
    const invoke = desktopApp(false)
    const view = show()
    const control = (await view.findByLabelText('开机时启动')) as HTMLInputElement
    await waitFor(() => expect(control.disabled).toBe(false))
    // Vuetify's switch reads the input event, as a person's click produces it.
    await fireEvent.input(control, { target: { checked: true } })
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('set_opens_at_login', { on: true }))
    await waitFor(() => expect(control.checked).toBe(true))
  })

  it('stays off when the system refuses', async () => {
    desktopApp(false, true)
    const view = show()
    const control = (await view.findByLabelText('开机时启动')) as HTMLInputElement
    await waitFor(() => expect(control.disabled).toBe(false))
    // Vuetify's switch reads the input event, as a person's click produces it.
    await fireEvent.input(control, { target: { checked: true } })
    // 这一块留在屏幕上，失败就地说一声（§3.11），不再弹一条几秒就走的 toast。
    await waitFor(() => expect(view.getByText('保存失败')).toBeTruthy())
    expect(control.checked).toBe(false)
    expect(toast.error).not.toHaveBeenCalled()
  })
})
