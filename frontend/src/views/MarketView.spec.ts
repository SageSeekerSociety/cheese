// 市场（容器）：开页落在「技能与插件」，读公开目录与「我的一台机器」；没机器时目录照看，
// 只是装不了。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listMyDevices = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
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
  listMyDevices.mockReset().mockResolvedValue({ devices: [] })
  fetchSkillMarket.mockReset().mockResolvedValue(MARKET)
  fetchMachineSkill.mockReset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('MarketView', () => {
  it('开页就是技能目录：目录公开，没机器也能浏览，+ 按不动并说清原因', async () => {
    render(MarketView, { global: { plugins: plugins() } })
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

  it('技能页签：有机器时点 + 直接安装', async () => {
    listMyDevices.mockResolvedValue({ devices: DEVICES })
    fetchMachineSkill.mockResolvedValue({})
    render(MarketView, { global: { plugins: plugins() } })
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(0)

    const add = screen.getByLabelText(t('work.room.skills.marketInstall')) as HTMLButtonElement
    expect(add.disabled).toBe(false)
    await fireEvent.click(add)
    await vi.advanceTimersByTimeAsync(0)

    // 检验通过后直接安装：不再有 dry-run 那一步，`confirm` 由 API 层固定为真。
    expect(fetchMachineSkill).toHaveBeenCalledWith('dev1', 'https://github.com/o/r/tree/main/skills/pdf', null, 'skill')
  })

  describe('目录读失败', () => {
    const settle = async () => {
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(0)
    }
    const retryButton = () => screen.getByRole('button', { name: t('global.loadError.retry') })

    it('失败留在目录那块并给重试；网络回来后点重试就读出目录，不用整页刷新', async () => {
      fetchSkillMarket.mockRejectedValueOnce(new Error('offline'))
      render(MarketView, { global: { plugins: plugins() } })
      await settle()

      expect(screen.getByText(t('work.room.skills.marketFailed'))).toBeTruthy()
      expect(screen.getByText('offline')).toBeTruthy()
      expect(screen.queryByText('pdf')).toBeNull()

      await fireEvent.click(retryButton())
      await settle()

      expect(screen.getByText('pdf')).toBeTruthy()
      expect(screen.queryByText(t('work.room.skills.marketFailed'))).toBeNull()
    })

    it('重试又失败：仍是失败那块，换成这次的原因，重试还在', async () => {
      fetchSkillMarket.mockRejectedValueOnce(new Error('offline')).mockRejectedValueOnce(new Error('还是不通'))
      render(MarketView, { global: { plugins: plugins() } })
      await settle()

      await fireEvent.click(retryButton())
      await settle()

      expect(screen.getByText(t('work.room.skills.marketFailed'))).toBeTruthy()
      expect(screen.getByText('还是不通')).toBeTruthy()
      expect(screen.queryByText('offline')).toBeNull()
      expect(retryButton()).toBeTruthy()
    })

    it('重试那份还在路上时没有可再点的重试，读回后显示目录', async () => {
      fetchSkillMarket.mockRejectedValueOnce(new Error('offline'))
      render(MarketView, { global: { plugins: plugins() } })
      await settle()

      let finish: (v: typeof MARKET) => void = () => {}
      fetchSkillMarket.mockImplementationOnce(() => new Promise((r) => (finish = r)))
      await fireEvent.click(retryButton())
      await settle()

      // 在路上时那块是「读取中」，重试按钮让位：人没法在这时再发一份。
      expect(screen.getByText(t('work.room.skills.marketLoading'))).toBeTruthy()
      expect(screen.queryByRole('button', { name: t('global.loadError.retry') })).toBeNull()

      finish(MARKET)
      await settle()
      expect(screen.getByText('pdf')).toBeTruthy()
      expect(screen.queryByText(t('work.room.skills.marketFailed'))).toBeNull()
    })

    it('403 是不给看：说没权限，不摆重试', async () => {
      fetchSkillMarket.mockRejectedValueOnce(Object.assign(new Error('无权访问'), { status: 403 }))
      render(MarketView, { global: { plugins: plugins() } })
      await settle()

      expect(screen.getByText(t('global.loadError.forbidden'))).toBeTruthy()
      expect(screen.queryByRole('button', { name: t('global.loadError.retry') })).toBeNull()
    })
  })
})
