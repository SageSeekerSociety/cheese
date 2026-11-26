// 技能弹窗画出来的那几件事：市场三页签换得动、卡片上的 + 把「装什么」原样往上发
// （MCP 只读，没有 +）。落盘与取数不在这里 —— 那些是 `useAgentSkills` 的事。
//
// 机器那一段（扫描 / 采纳 / 退订）不在板上摆：装进来或勾上就跟着这位队友走。
// 板上只画队友身上真有的：机器上可加、还没带的那几条不算。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import AgentSkillsDialog from './AgentSkillsDialog.vue'

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

/** 这几个技能名里，哪些列在「米洛 的技能」那份名单上。 */
async function listed(...names: string[]) {
  const board = await screen.findByRole('list', { name: t('work.room.skills.carriedTitle', { name: '米洛' }) })
  return names.filter((name) => within(board).queryByText(name, { exact: true }))
}

describe('技能弹窗', () => {
  it('灯板画队友身上的技能：带上的，加上平台与项目给每位队友都发的；机器上没带的不画', async () => {
    mount({
      available: [
        { name: 'cheese', description: '芝士自己的用法', source: 'project' as const },
        { name: 'ponytail', description: '懒，但把活干对的写法', source: 'machine' as const },
      ],
    })

    expect(await listed('wolfram', 'cheese', 'ponytail')).toEqual(['wolfram', 'cheese'])
  })

  it('什么都没带、只有机器上的 pdf 可加：pdf 不进名单，说「还什么都没带」', async () => {
    mount({
      carried: [],
      available: [{ name: 'pdf', description: '读 PDF', source: 'machine' as const }],
      market: null,
    })

    expect(await screen.findByText(t('work.room.skills.emptyCarried'))).toBeTruthy()
    expect(await listed('pdf')).toEqual([])
  })

  it('机器上的技能带上了就画，灯照旧只说能不能用', async () => {
    mount({
      carried: [{ name: 'pdf', description: '读 PDF', source: 'machine' as const }],
      available: [{ name: 'wolfram', description: '算', source: 'platform' as const }],
      health: { wolfram: { status: 'unavailable', detail: '没配钥匙' } },
      market: null,
    })

    expect(await listed('pdf', 'wolfram')).toEqual(['pdf', 'wolfram'])
    expect(screen.getByRole('listitem', { name: t('work.room.skills.socketLit', { name: 'pdf' }) })).toBeTruthy()
    expect(screen.getByRole('listitem', { name: t('work.room.skills.socketUnlit', { name: 'wolfram' }) })).toBeTruthy()
    expect(screen.queryByText(t('work.room.skills.emptyCarried'))).toBeNull()
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

// 已带的机器技能能去掉：点一下就是「从这位队友身上拿掉它」，落库与失败回滚在 `useAgentSkills`。
// 平台和项目发给每位队友的那几格去掉了也照样在板上（它们本来就到了每个执行器），点下去看着像
// 没反应，所以这几格不给按钮，不管它在不在队友的名单上。
const PDF = { name: 'pdf', description: '读 PDF', source: 'machine' as const }

describe('去掉已带的技能', () => {
  it('点已带的 pdf，把它的名字往上发；平台与项目发给每位队友的那几格没有这颗按钮', async () => {
    const view = mount({
      carried: [PDF, { name: 'wolfram', description: '算', source: 'platform' as const }],
      available: [{ name: 'cheese', description: '芝士自己的用法', source: 'project' as const }],
      market: null,
    })

    await fireEvent.click(await screen.findByRole('button', { name: '从米洛身上去掉 pdf' }))
    expect(view.emitted('remove')).toEqual([['pdf']])
    expect(screen.getByText('wolfram')).toBeTruthy()
    expect(screen.getByText('cheese')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /wolfram|cheese/ })).toBeNull()
  })

  it('别人正占着「编辑中」时按不动，什么都不往上发', async () => {
    const view = mount({ carried: [PDF], editingBlocked: true, editingBy: 'alice', market: null })

    const socket = (await screen.findByRole('button', { name: '从米洛身上去掉 pdf' })) as HTMLButtonElement
    expect(socket.disabled).toBe(true)
    await fireEvent.click(socket)
    expect(view.emitted('remove')).toBeUndefined()
  })

  it('上一笔还在保存时按不动', async () => {
    mount({ carried: [PDF], saving: true, market: null })

    const socket = (await screen.findByRole('button', { name: '从米洛身上去掉 pdf' })) as HTMLButtonElement
    expect(socket.disabled).toBe(true)
  })
})
