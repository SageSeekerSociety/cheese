// 市场（容器）：节点状态板在「模型与工作电脑」页签上。默认页签不读节点；第一次点开那个
// 页签才读、才起 15 秒的表；切回别的页签表照走（和板子在不设 eager 的 v-window-item 里一样）。
// 技能页签同理：第一次点开才读公开目录与「我的一台机器」；没机器时目录照看，只是装不了。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMarketNodes = vi.fn()
const getMarketPools = vi.fn()
const listMyDevices = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getMarketNodes: (...a: unknown[]) => getMarketNodes(...a),
    getMarketPools: (...a: unknown[]) => getMarketPools(...a),
    listMyDevices: (...a: unknown[]) => listMyDevices(...a),
  }
})

const fetchSkillMarket = vi.fn()
const fetchMachineSkill = vi.fn()
vi.mock('@/api/machineSkills', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/machineSkills')>()),
  fetchSkillMarket: () => fetchSkillMarket(),
  fetchMachineSkill: (...a: unknown[]) => fetchMachineSkill(...a),
}))

import { MARKET_NODES_REFRESH_MS } from '@/composables/useMarketNodes'

import MarketView from './MarketView.vue'

import i18n, { setLocale, t } from '@/i18n'

const MARKET = {
  skills: [
    {
      name: 'pdf',
      description: '读 PDF',
      url: 'https://github.com/o/r/tree/main/skills/pdf',
      source: 'anthropic',
      kind: 'skill' as const,
    },
  ],
  plugins: [
    {
      name: 'reviewer',
      description: '看代码',
      url: 'https://x/plugins/reviewer',
      source: 'anthropic',
      kind: 'plugin' as const,
    },
  ],
  plugin_total: 315,
  mcp: [{ name: 'Acme', description: '一个 MCP server', url: '', source: 'mcp', kind: 'mcp' as const }],
  notes: [],
}

const DEVICES = [{ device_id: 'dev1', name: '我的电脑', online: true, project_ids: [], team_ids: [], screens: [] }]

const plugins = () => [createVuetify({ components, directives }), i18n]

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 确认框是个 v-overlay，打开时要量视口；jsdom 两样都没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  getMarketPools.mockReset().mockResolvedValue({ ai: [], compute: [] })
  getMarketNodes.mockReset().mockResolvedValue({ nodes: [], active_turns_total: 0, current_provider: 'standard' })
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  fetchSkillMarket.mockReset().mockResolvedValue(MARKET)
  fetchMachineSkill.mockReset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('MarketView', () => {
  it('polls nodes only once the pools tab has been opened, and keeps polling after leaving it', async () => {
    render(MarketView, { global: { plugins: plugins() } })
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS * 2)
    expect(getMarketPools).toHaveBeenCalledTimes(1)
    expect(getMarketNodes).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByText(t('market.tabs.pools')))
    await vi.advanceTimersByTimeAsync(0)
    expect(getMarketNodes).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(2)

    await fireEvent.click(screen.getByText(t('market.tabs.tasks')))
    await vi.advanceTimersByTimeAsync(MARKET_NODES_REFRESH_MS)
    expect(getMarketNodes).toHaveBeenCalledTimes(3)
  })

  it('技能页签：目录公开，没机器也能浏览，+ 按不动并说清原因', async () => {
    render(MarketView, { global: { plugins: plugins() } })
    // 没点开技能页签之前不读公开目录。
    expect(fetchSkillMarket).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByText(t('market.tabs.skills')))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)

    expect(fetchSkillMarket).toHaveBeenCalledTimes(1)
    expect(listMyDevices).toHaveBeenCalledTimes(1)
    // 浏览不需要机器：目录照常出来。
    expect(screen.getByText('pdf')).toBeTruthy()
    // 装要一台机器：+ 画出来了，但按不动，并说清原因。
    const add = screen.getByLabelText(t('work.room.skills.marketInstall')) as HTMLButtonElement
    expect(add.disabled).toBe(true)
    expect(screen.getByText(t('work.room.skills.marketNeedMachine'))).toBeTruthy()
  })

  it('技能页签：有机器时点 + 先只看不落（dry-run）', async () => {
    listMyDevices.mockResolvedValue({ devices: DEVICES })
    fetchMachineSkill.mockResolvedValue({
      landed: false,
      plan: [{ name: 'pdf', description: '读 PDF', files: ['SKILL.md'] }],
    })
    render(MarketView, { global: { plugins: plugins() } })

    await fireEvent.click(screen.getByText(t('market.tabs.skills')))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)

    const add = screen.getByLabelText(t('work.room.skills.marketInstall')) as HTMLButtonElement
    expect(add.disabled).toBe(false)
    await fireEvent.click(add)
    await vi.advanceTimersByTimeAsync(0)

    // 第一次是 dry-run：先看会写什么，没落盘。
    expect(fetchMachineSkill).toHaveBeenCalledWith(
      'dev1',
      'https://github.com/o/r/tree/main/skills/pdf',
      null,
      'skill',
      false
    )
  })
})
