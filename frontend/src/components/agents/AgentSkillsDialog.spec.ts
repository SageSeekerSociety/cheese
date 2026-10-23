// 技能弹窗画出来的那几件事：市场三页签换得动、卡片上的 + 把「装什么」原样往上发
// （MCP 只读，没有 +）。落盘与取数不在这里 —— 那些是 `useAgentSkills` 的事。
//
// 机器那一段（扫描 / 采纳 / 退订）不在板上摆：装进来或勾上就跟着这位队友走。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import AgentSkillsDialog from './AgentSkillsDialog.vue'

import { setLocale } from '@/i18n'

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
  setLocale('zh-CN')
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
  ;(window as unknown as { innerWidth: number }).innerWidth = 1280
})
afterAll(() => vi.unstubAllGlobals())
afterEach(() => {
  document.body.innerHTML = ''
})

function mount(extra: Record<string, unknown> = {}) {
  return render(AgentSkillsDialog, {
    props: {
      modelValue: true,
      agentName: '米洛',
      carried: [{ name: 'wolfram', description: '算', source: 'platform' as const }],
      available: [],
      health: {},
      deviceId: 'dev2',
      market: MARKET,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('技能弹窗', () => {
  it('把已装备和可用技能合并显示在灯板，移除下方技能列表', async () => {
    mount({
      available: [
        { name: 'cheese', description: '芝士自己的用法', source: 'project' as const },
        { name: 'ponytail', description: '懒，但把活干对的写法', source: 'machine' as const },
      ],
    })

    expect(await screen.findByText('wolfram')).toBeTruthy()
    expect(screen.getByText('cheese')).toBeTruthy()
    expect(screen.getByText('ponytail')).toBeTruthy()
    expect(document.querySelectorAll('.skills__grid .socket')).toHaveLength(3)
    expect(document.querySelector('.pool')).toBeNull()
    expect(document.querySelector('.machine')).toBeNull()
    expect(screen.queryByText(/工作电脑/)).toBeNull()
    expect(screen.queryByText('添加')).toBeNull()
  })

  it('市场三页签各画各的，MCP 那张没有 +', async () => {
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
    expect(screen.getByText('只读')).toBeTruthy()
  })

  it('卡片上的 + 把地址和 kind 原样往上发', async () => {
    const view = mount()

    await fireEvent.click(await screen.findByLabelText('装到这台机器上'))
    expect(view.emitted('install')?.[0]).toEqual([
      { url: 'https://github.com/o/r/tree/main/skills/pdf', kind: 'skill' },
    ])

    await fireEvent.click(screen.getByRole('tab', { name: '插件' }))
    await fireEvent.click(screen.getByLabelText('装到这台机器上'))
    expect(view.emitted('install')?.[1]).toEqual([
      { url: 'https://github.com/o/r/tree/main/plugins/reviewer', kind: 'plugin' },
    ])
  })

  it('没有机器时 + 按不动：落盘总得有个落点', async () => {
    mount({ deviceId: null })

    const button = await screen.findByLabelText('装到这台机器上')
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('别人正占着「编辑中」：先说这一句，灯板转只读', async () => {
    mount({ editingBlocked: true, editingBy: 'alice', health: { wolfram: { status: 'ok', detail: '' } } })

    expect(await screen.findByText('alice 正在编辑这个队友，等他改完再试')).toBeTruthy()
    expect(screen.queryByText('只有项目管理员能改队友带的技能。')).toBeNull()
    expect(document.querySelector('.socket--on')).toBeTruthy()
  })

  it('占着的那位没留下名字：说什么都不假造一个名字', async () => {
    mount({ editingBlocked: true })

    expect(await screen.findByText('有人正在编辑这个队友，等他改完再试')).toBeTruthy()
  })
})
