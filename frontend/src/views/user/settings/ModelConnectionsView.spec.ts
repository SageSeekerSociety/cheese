// 「模型连接」上人说得出的几条：每份连接收起时只是一行，点开才有它的操作，再点收起；
// 解除要先确认，取消了就什么也没发生，确认解除的是点开的那一份；已在解除中的那一份不能再
// 解除；检查没通过的那一份收起时就看得出，点开看得到原因；两类连接各自空着时各说各的；
// 自定义模型服务填齐了才交出去，交出去的就是选的设备和填的三样。
import type { AccountConnection, ModelConnection } from '@/types/modelConnections'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
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
    id: deviceId,
    local_id: 'default',
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

function row(id: string): HTMLElement {
  return document.querySelector(`[data-connection="${id}"]`) as HTMLElement
}

/** 那一行的摘要：整行是一颗开合的按钮。 */
function summary(id: string): HTMLElement {
  return within(row(id)).getAllByRole('button')[0]
}

async function expand(id: string) {
  await fireEvent.click(summary(id))
}

const REMOVE = () => ({ name: t('account.modelConnections.remove') })

describe('ModelConnectionsView', () => {
  it('shows a connection as one line until it is opened, and closes it again', async () => {
    mount([connection('desk')])

    expect(summary('desk').getAttribute('aria-expanded')).toBe('false')
    expect(within(row('desk')).queryByRole('button', REMOVE())).toBeNull()

    await expand('desk')
    expect(summary('desk').getAttribute('aria-expanded')).toBe('true')
    for (const name of ['recheck', 'rename', 'remove']) {
      expect(within(row('desk')).getByRole('button', { name: t(`account.modelConnections.${name}`) })).toBeTruthy()
    }

    await expand('desk')
    expect(summary('desk').getAttribute('aria-expanded')).toBe('false')
    await waitFor(() => expect(within(row('desk')).queryByRole('button', REMOVE())).toBeNull())
  })

  it('opens only the line that was clicked', async () => {
    mount([connection('desk'), connection('laptop')])
    await expand('laptop')
    expect(within(row('laptop')).getByRole('button', REMOVE())).toBeTruthy()
    expect(within(row('desk')).queryByRole('button', REMOVE())).toBeNull()
  })

  it('removes only the selected login when two logins share a computer', async () => {
    const view = mount([connection('login-a', { device_id: 'desk' }), connection('login-b', { device_id: 'desk' })])
    await expand('login-a')
    await fireEvent.click(within(row('login-a')).getByRole('button', REMOVE()))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', REMOVE()))
    expect(view.emitted('revoke')).toEqual([['login-a']])
    await expand('login-b')
    expect(within(row('login-b')).getByRole('button', REMOVE())).toBeTruthy()
  })

  it('removes only after confirming, and only the connection asked for', async () => {
    const view = mount([connection('desk'), connection('laptop')])
    await expand('laptop')

    await fireEvent.click(within(row('laptop')).getByRole('button', REMOVE()))
    await fireEvent.click(await screen.findByRole('button', { name: t('global.cancel') }))
    expect(view.emitted('revoke')).toBeUndefined()

    await fireEvent.click(within(row('laptop')).getByRole('button', REMOVE()))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', REMOVE()))
    expect(view.emitted('revoke')).toEqual([['laptop']])
  })

  it('offers no second removal of a connection already being removed', async () => {
    mount([connection('lab', { online: false, logged_in: false, revoking: true })])

    expect(summary('lab').textContent).toContain(t('account.modelConnections.revoking'))
    await expand('lab')
    expect(within(row('lab')).queryByRole('button', REMOVE())).toBeNull()
  })

  it('says a key failed its check while closed, and why once opened', async () => {
    mount([], [], [{ ...heldKey('bad'), check_error: 'HTTP 401 invalid x-api-key' }, heldKey('good')])

    expect(summary('bad').textContent).toContain(t('account.modelConnections.checkFailedState'))
    expect(summary('good').textContent).not.toContain(t('account.modelConnections.checkFailedState'))
    expect(row('bad').textContent).not.toContain('HTTP 401 invalid x-api-key')

    await expand('bad')
    expect(row('bad').textContent).toContain('HTTP 401 invalid x-api-key')
  })

  it('says which kind of connection is missing, and still offers each way to add one', () => {
    mount([], [], [heldKey('one')])

    const devices = screen.getByRole('region', { name: t('account.modelConnections.devicesTitle') })
    const account = screen.getByRole('region', { name: t('account.modelConnections.heldTitle') })
    expect(devices.textContent).toContain(t('account.modelConnections.devicesEmpty'))
    expect(account.textContent).not.toContain(t('account.modelConnections.heldEmpty'))
    expect(within(devices).getByRole('button', { name: t('account.modelConnections.addService') })).toBeTruthy()
    expect(within(account).getByRole('button', { name: t('account.modelConnections.addApiKey') })).toBeTruthy()
    expect(within(account).getByRole('button', { name: t('account.modelConnections.addAccountService') })).toBeTruthy()

    cleanup()
    mount([connection('desk')])
    expect(screen.getByRole('region', { name: t('account.modelConnections.heldTitle') }).textContent).toContain(
      t('account.modelConnections.heldEmpty')
    )
    expect(screen.getByRole('region', { name: t('account.modelConnections.devicesTitle') }).textContent).not.toContain(
      t('account.modelConnections.devicesEmpty')
    )
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
    await expand('two')

    await fireEvent.click(within(row('two')).getByRole('button', REMOVE()))
    await fireEvent.click(await screen.findByRole('button', { name: t('global.cancel') }))
    expect(view.emitted('removeHeld')).toBeUndefined()

    await fireEvent.click(within(row('two')).getByRole('button', REMOVE()))
    const dialog = await screen.findByRole('alertdialog')
    await fireEvent.click(within(dialog).getByRole('button', REMOVE()))
    expect(view.emitted('removeHeld')).toEqual([['two']])
  })

  it('names the login on the device whose row was asked, and nothing on the account', async () => {
    const view = mount([connection('desk'), connection('laptop')], [], [heldKey('one')])
    await expand('laptop')

    await fireEvent.click(within(row('laptop')).getByRole('button', { name: t('account.modelConnections.rename') }))
    const field = await screen.findByLabelText(t('account.modelConnections.name'))
    await fireEvent.update(field, '  Lab Max  ')
    await fireEvent.click(screen.getByRole('button', { name: t('global.save') }))

    expect(view.emitted('renameDevice')).toEqual([['laptop', 'Lab Max']])
    expect(view.emitted('renameHeld')).toBeUndefined()
  })
})
