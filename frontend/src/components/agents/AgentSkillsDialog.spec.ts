// 技能弹窗画出来的那几件事：市场三页签换得动、卡片上的 + 把「装什么」原样往上发
// （MCP 只读，没有 +）。落盘与取数不在这里 —— 那些是 `useAgentSkills` 的事。
//
// 机器那一段（扫描 / 采纳 / 退订）不在板上摆：装进来或勾上就跟着这位队友走。
// 板上只画队友身上真有的：机器上可加、还没带的那几条不算。
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
  it('灯板画队友身上的技能：带上的，加上平台与项目给每位队友都发的；机器上没带的不画', async () => {
    mount({
      available: [
        { name: 'cheese', description: '芝士自己的用法', source: 'project' as const },
        { name: 'ponytail', description: '懒，但把活干对的写法', source: 'machine' as const },
      ],
    })

    expect(await screen.findByText('wolfram')).toBeTruthy()
    expect(screen.getByText('cheese')).toBeTruthy()
    expect(screen.queryByText('ponytail')).toBeNull()
    expect(document.querySelectorAll('.skills__grid .socket')).toHaveLength(2)
    expect(document.querySelector('.pool')).toBeNull()
    expect(document.querySelector('.machine')).toBeNull()
    expect(screen.queryByText(/工作电脑/)).toBeNull()
    expect(screen.queryByText('添加')).toBeNull()
  })

  it('什么都没带、只有机器上的 pdf 可加：pdf 不进名单，说「还什么都没带」', async () => {
    mount({
      carried: [],
      available: [{ name: 'pdf', description: '读 PDF', source: 'machine' as const }],
      market: null,
    })

    expect(await screen.findByText('这个队友还什么都没带。')).toBeTruthy()
    expect(document.querySelectorAll('.skills__grid .socket')).toHaveLength(0)
    expect(screen.queryByText('pdf')).toBeNull()
  })

  it('机器上的技能带上了就画，灯照旧只说能不能用', async () => {
    mount({
      carried: [{ name: 'pdf', description: '读 PDF', source: 'machine' as const }],
      available: [{ name: 'wolfram', description: '算', source: 'platform' as const }],
      health: { wolfram: { status: 'unavailable', detail: '没配钥匙' } },
      market: null,
    })

    const sockets = [...document.querySelectorAll('.skills__grid .socket')]
    expect(await screen.findByText('pdf')).toBeTruthy()
    expect(sockets.map((s) => s.querySelector('.socket__name')?.textContent)).toEqual(['pdf', 'wolfram'])
    expect(sockets.map((s) => s.classList.contains('socket--on'))).toEqual([true, false])
    expect(screen.queryByText('这个队友还什么都没带。')).toBeNull()
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

  it('别人正占着「编辑中」：市场卡片上的 + 也按不动', async () => {
    mount({ editingBlocked: true, editingBy: 'alice' })

    const button = await screen.findByLabelText('装到这台机器上')
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('占着的那位没留下名字：说什么都不假造一个名字', async () => {
    mount({ editingBlocked: true })

    expect(await screen.findByText('有人正在编辑这个队友，等他改完再试')).toBeTruthy()
  })
})

// 已带的那几格能去掉：点一下就是「从这位队友身上拿掉它」，落库与失败回滚在 `useAgentSkills`。
// 平台和项目给每位队友都发的那几格不在队友的名单上，没有可去掉的，所以不是按钮。
describe('去掉已带的技能', () => {
  it('点已带的那一格，把它的名字往上发；平台给大家的那格不能点', async () => {
    const view = mount({
      available: [{ name: 'cheese', description: '芝士自己的用法', source: 'project' as const }],
      market: null,
    })

    await fireEvent.click(await screen.findByRole('button', { name: '从米洛身上去掉 wolfram' }))
    expect(view.emitted('remove')).toEqual([['wolfram']])
    expect(screen.getByText('cheese')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /cheese/ })).toBeNull()
  })

  it('别人正占着「编辑中」时按不动，什么都不往上发', async () => {
    const view = mount({ editingBlocked: true, editingBy: 'alice', market: null })

    const socket = (await screen.findByRole('button', { name: '从米洛身上去掉 wolfram' })) as HTMLButtonElement
    expect(socket.disabled).toBe(true)
    await fireEvent.click(socket)
    expect(view.emitted('remove')).toBeUndefined()
  })

  it('上一笔还在保存时按不动', async () => {
    mount({ saving: true, market: null })

    const socket = (await screen.findByRole('button', { name: '从米洛身上去掉 wolfram' })) as HTMLButtonElement
    expect(socket.disabled).toBe(true)
  })
})
