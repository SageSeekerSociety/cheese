// 共享的技能市场（SkillMarket）画出来的那几件事：前三个页签各画各的（MCP 只有展示、
// 没有 +）、第四个货架「Agent 模板」只有入口没有货品、卡片上的 + 把「装什么」原样往上
// 发、没机器时 + 按不动。取数与落盘不在这一层 —— 那些是宿主页面 / useAgentSkills 的事。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import SkillMarket from './SkillMarket.vue'

import { setLocale, t } from '@/i18n'

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
      url: 'https://github.com/o/r/tree/main/plugins/reviewer',
      source: 'anthropic',
      kind: 'plugin' as const,
    },
  ],
  plugin_total: 315,
  mcp: [{ name: 'Acme', description: '一个 MCP server', url: '', source: 'mcp', kind: 'mcp' as const }],
  notes: [],
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
beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(props: Record<string, unknown> = {}) {
  return render(SkillMarket, {
    props: { market: MARKET, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('技能市场（共享）', () => {
  it('前三个页签各画各的：MCP 那张只有展示，没有 +', async () => {
    mount()

    expect(await screen.findByText('pdf')).toBeTruthy()
    expect(screen.getByText('Anthropic')).toBeTruthy()

    await fireEvent.click(screen.getByRole('tab', { name: '插件' }))
    expect(screen.getByText('reviewer')).toBeTruthy()
    expect(screen.queryByText('pdf')).toBeNull()
    expect(screen.getByText('这里列出前 1 条，官方目录共 315 条。')).toBeTruthy()

    await fireEvent.click(screen.getByRole('tab', { name: 'MCP' }))
    expect(screen.getByText('Acme')).toBeTruthy()
    expect(screen.queryByLabelText('装到这台机器上')).toBeNull()
  })

  it('不在市场里时不摆那一格（队友技能面板就是这样用它的）', () => {
    mount()
    expect(screen.queryByRole('tab', { name: 'Agent 模板' })).toBeNull()
  })

  it('第四个货架「Agent 模板」只有入口：照实说还没有，不摆任何卡片', async () => {
    mount({ showAgents: true })

    await fireEvent.click(screen.getByRole('tab', { name: 'Agent 模板' }))
    expect(screen.getByText('还没有 Agent 模板。')).toBeTruthy()
    // 这格背后没有目录、没有数据：一张卡片也不摆（连技能页那张 pdf 也不在）。
    expect(screen.queryByRole('button', { name: t('work.room.skills.marketInstall') })).toBeNull()
    expect(screen.queryByText('pdf')).toBeNull()
  })

  it('卡片上的 + 把地址和 kind 原样往上发', async () => {
    const view = mount({ deviceId: 'dev1' })

    await fireEvent.click(await screen.findByLabelText('装到这台机器上'))
    expect(view.emitted('install')?.[0]).toEqual([
      { url: 'https://github.com/o/r/tree/main/skills/pdf', kind: 'skill' },
    ])
  })

  it('没有机器时 + 按不动：落盘总得有个落点', async () => {
    mount({ deviceId: null })

    const button = await screen.findByLabelText(t('work.room.skills.marketInstall'))
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(button.getAttribute('title')).toBe(t('work.room.skills.noMachine'))
  })
})
