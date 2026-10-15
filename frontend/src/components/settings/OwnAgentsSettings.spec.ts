// 项目设置里成员自己的 Claude Code（#2991）。一个人每份模型连接可以加入一位，由他自己加入、
// 自己移出；别人那几行只看得到名字、主人和连接类型，没有加入、移出，也看不到设备。
import type { OwnAgentsSettings as Settings } from '@/types/ownAgents'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import * as ownAgents from '../../api/ownAgents'

import OwnAgentsSettings from './OwnAgentsSettings.vue'

import { setLocale } from '@/i18n'

vi.mock('../../api/ownAgents')
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

const SETTINGS: Settings = {
  allowed: true,
  kinds: ['subscription', 'api_key', 'model_service'],
  service_hosts: [],
  can_manage: false,
  agents: [
    {
      handle: 'agent-alice-max',
      // i18n-data: an agent's stored name, not interface copy
      name: 'alice的 Claude Code（Max）',
      callers: 'owner',
      owner_handle: 'alice',
      owner_name: 'alice',
      connection: { kind: 'subscription', plan: 'max', model: null, on_account: false, allowed: true },
      machine: { name: 'alice-laptop', online: true },
    },
    {
      handle: 'agent-bob-api',
      // i18n-data: an agent's stored name, not interface copy
      name: 'bob的 Claude Code（API）',
      callers: 'owner',
      owner_handle: 'bob',
      owner_name: 'bob',
      connection: { kind: 'api_key', plan: null, model: null, on_account: true, allowed: true },
      machine: null,
    },
  ],
  my_connections: [
    {
      id: 'alice-laptop',
      kind: 'subscription',
      plan: 'max',
      model: null,
      name: null,
      service_host: null,
      on_account: false,
      machine_name: 'alice-laptop',
      online: true,
      added: true,
      allowed: true,
    },
    {
      id: 'alice-desk',
      kind: 'model_service',
      plan: null,
      model: 'kimi-k2',
      name: null,
      service_host: 'api.moonshot.cn',
      on_account: false,
      machine_name: 'alice-desk',
      online: false,
      added: false,
      allowed: true,
    },
    {
      id: '6f1c2b1e-account-key',
      kind: 'api_key',
      plan: null,
      model: null,
      name: 'work',
      service_host: null,
      on_account: true,
      machine_name: null,
      online: true,
      added: false,
      allowed: true,
    },
  ],
}

function rowOf(name: string): HTMLElement {
  const row = screen.getAllByTestId('own-agent').find((el) => el.textContent?.includes(name))
  if (!row) throw new Error(`no row for ${name}`)
  return row
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  vi.mocked(ownAgents.getOwnAgents).mockResolvedValue(SETTINGS)
  vi.mocked(ownAgents.addOwnAgent).mockResolvedValue(SETTINGS)
  vi.mocked(ownAgents.removeOwnAgent).mockResolvedValue(SETTINGS)
})

afterEach(cleanup)

async function mountSettings() {
  render(OwnAgentsSettings, {
    props: { projectId: 'project' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await vi.waitFor(() => expect(screen.getAllByTestId('own-agent')).toHaveLength(2))
}

describe('members’ own Claude Code in project settings', () => {
  it('adds one of my connections that is not in the project yet', async () => {
    await mountSettings()

    const offers = screen.getAllByTestId('own-agent-offer')
    expect(offers).toHaveLength(2)
    await fireEvent.click(offers[0].querySelector('[data-testid="own-agent-add"]')!)

    expect(ownAgents.addOwnAgent).toHaveBeenCalledWith('project', 'alice-desk')
  })

  it('adds a connection my account holds, which no machine has to be online for', async () => {
    await mountSettings()

    const offer = screen.getAllByTestId('own-agent-offer').find((el) => el.textContent?.includes('work'))!
    await fireEvent.click(offer.querySelector('[data-testid="own-agent-add"]')!)

    expect(ownAgents.addOwnAgent).toHaveBeenCalledWith('project', '6f1c2b1e-account-key')
  })

  it('lets a manager allow a provider by its host name, and sends nothing that is not one', async () => {
    const managed = { ...SETTINGS, can_manage: true }
    vi.mocked(ownAgents.getOwnAgents).mockResolvedValue(managed)
    vi.mocked(ownAgents.setOwnAgentsRules).mockResolvedValue({ ...managed, service_hosts: ['api.moonshot.cn'] })
    await mountSettings()

    const input = screen.getByTestId('own-agents-host-input').querySelector('input')!
    const addButton = screen.getByTestId('own-agents-host-add')
    await fireEvent.update(input, 'https://api.moonshot.cn/anthropic')
    expect(addButton.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(addButton)
    expect(ownAgents.setOwnAgentsRules).not.toHaveBeenCalled()

    await fireEvent.update(input, ' API.Moonshot.cn ')
    await fireEvent.click(addButton)

    expect(ownAgents.setOwnAgentsRules).toHaveBeenCalledWith('project', { service_hosts: ['api.moonshot.cn'] })
  })

  it('removes my own agent, and offers no removal on anyone else’s', async () => {
    await mountSettings()

    expect(rowOf('bob的').querySelector('[data-testid="own-agent-remove"]')).toBeNull()
    await fireEvent.click(rowOf('alice的').querySelector('[data-testid="own-agent-remove"]')!)

    expect(ownAgents.removeOwnAgent).toHaveBeenCalledWith('project', 'agent-alice-max')
  })
})
