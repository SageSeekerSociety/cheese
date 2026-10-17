// 「模型连接」上人说得出的几条：解除要先确认，取消了就什么也没发生；确认解除的是那一行
// 那台设备的连接；已在解除中的那一份不能再解除；自定义模型服务填齐了才交出去，交出去的
// 就是选的设备和填的三样。
import type { AccountConnection, ModelConnection } from '@/types/modelConnections'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ModelConnectionsView from './ModelConnectionsView.vue'

import { setLocale, t } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function connection(deviceId: string, extra: Partial<ModelConnection> = {}): ModelConnection {
  return {
    device_id: deviceId,
    device_name: deviceId,
    online: true,
    installed: true,
    logged_in: true,
    auth_method: 'claude.ai',
    subscription_type: 'max',
    model: null,
    service_host: null,
    name: null,
    checked_at: new Date().toISOString(),
    last_used_at: null,
    revoking: false,
    ...extra,
  }
}

function heldKey(id: string): AccountConnection {
  return {
    id,
    kind: 'api_key',
    name: null,
    key_last4: 'Q7zK',
    service_host: null,
    model: null,
    checked_at: new Date().toISOString(),
    check_error: null,
    created_at: new Date().toISOString(),
  }
}

function mount(
  connections: ModelConnection[],
  devices = [{ device_id: 'desk', name: 'desk' }],
  held: AccountConnection[] = []
) {
  return render(ModelConnectionsView, {
    props: { loading: false, error: null, connections, held, devices, busy: '', saved: 0 },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

function row(deviceId: string): HTMLElement {
  return document.querySelector(`[data-connection="${deviceId}"]`) as HTMLElement
}

describe('ModelConnectionsView', () => {
  it('removes only after confirming, and only the connection asked for', async () => {
    const view = mount([connection('desk'), connection('laptop')])

    await fireEvent.click(within(row('laptop')).getByRole('button', { name: t('account.modelConnections.remove') }))
    await fireEvent.click(await screen.findByRole('button', { name: t('global.cancel') }))
    expect(view.emitted('revoke')).toBeUndefined()

    await fireEvent.click(within(row('laptop')).getByRole('button', { name: t('account.modelConnections.remove') }))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: t('account.modelConnections.remove') }))
    expect(view.emitted('revoke')).toEqual([['laptop']])
  })

  it('offers no second removal of a connection already being removed', () => {
    mount([connection('lab', { online: false, logged_in: false, revoking: true })])

    expect(within(row('lab')).queryByRole('button', { name: t('account.modelConnections.remove') })).toBeNull()
  })

  it('hands a complete model service to the chosen device, and nothing incomplete', async () => {
    const view = mount([])
    await fireEvent.click(screen.getByRole('button', { name: t('account.modelConnections.addService') }))
    const save = await screen.findByRole('button', { name: t('account.thisDevice.modelServiceSave') })

    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceUrl')), 'https://kimi.test')
    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceToken')), 'sk-1')
    await fireEvent.click(save)
    expect(view.emitted('service')).toBeUndefined()

    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceModel')), 'k2')
    await fireEvent.click(save)
    expect(view.emitted('service')).toEqual([['desk', { baseUrl: 'https://kimi.test', token: 'sk-1', model: 'k2' }]])
  })

  it('keeps an API key on the account only once one is typed, exactly as typed', async () => {
    const view = mount([])
    await fireEvent.click(screen.getByRole('button', { name: t('account.modelConnections.addApiKey') }))
    const save = await screen.findByRole('button', { name: t('global.save') })

    await fireEvent.click(save)
    expect(view.emitted('addHeld')).toBeUndefined()

    await fireEvent.update(screen.getByLabelText(t('account.modelConnections.apiKeyLabel')), ' sk-ant-1 ')
    await fireEvent.click(save)
    expect(view.emitted('addHeld')).toEqual([[{ kind: 'api_key', key: 'sk-ant-1', name: '', baseUrl: '', model: '' }]])
  })

  it('keeps a model service on the account only with an https address and a model', async () => {
    const view = mount([])
    await fireEvent.click(screen.getByRole('button', { name: t('account.modelConnections.addAccountService') }))
    const save = await screen.findByRole('button', { name: t('global.save') })

    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceUrl')), 'http://kimi.test')
    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceModel')), 'k2')
    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceToken')), 'sk-1')
    await fireEvent.click(save)
    expect(view.emitted('addHeld')).toBeUndefined()

    await fireEvent.update(screen.getByLabelText(t('account.thisDevice.modelServiceUrl')), 'https://kimi.test')
    await fireEvent.click(save)
    expect(view.emitted('addHeld')).toEqual([
      [{ kind: 'model_service', key: 'sk-1', name: '', baseUrl: 'https://kimi.test', model: 'k2' }],
    ])
  })

  it('removes a key on the account only after confirming, and only that one', async () => {
    const view = mount([], [], [heldKey('one'), heldKey('two')])

    await fireEvent.click(within(row('two')).getByRole('button', { name: t('account.modelConnections.remove') }))
    await fireEvent.click(await screen.findByRole('button', { name: t('global.cancel') }))
    expect(view.emitted('removeHeld')).toBeUndefined()

    await fireEvent.click(within(row('two')).getByRole('button', { name: t('account.modelConnections.remove') }))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', { name: t('account.modelConnections.remove') }))
    expect(view.emitted('removeHeld')).toEqual([['two']])
  })

  it('names the login on the device whose row was asked, and nothing on the account', async () => {
    const view = mount([connection('desk'), connection('laptop')], [], [heldKey('one')])

    await fireEvent.click(within(row('laptop')).getByRole('button', { name: t('account.modelConnections.rename') }))
    const field = await screen.findByLabelText(t('account.modelConnections.name'))
    await fireEvent.update(field, '  Lab Max  ')
    await fireEvent.click(screen.getByRole('button', { name: t('global.save') }))

    expect(view.emitted('renameDevice')).toEqual([['laptop', 'Lab Max']])
    expect(view.emitted('renameHeld')).toBeUndefined()
  })
})
