// 「AI 队友」—— 项目设置里的一节。五件事值得被盯着，都是渲染不会失败但人会被误导的：
//   1. 一行写着这个队友跑在哪个模型上、思考强度是哪一档；跟随项目时写出项目那个模型
//   2. 后端那一半还没上线时，这一节得说「还没上线」，不能是白屏也不能是报错
//   3. 空名册要说清楚队友是什么、能拿它干嘛，不能只画个空盒子
//   4. 停用必须先问一遍，并且说明「已经在用的话题照常工作」
//   5. 记忆不在这一页：它由平台统一管理，不是队友的一项设置
import type { ProjectAgent } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectAgents = vi.fn()
const listAgentTypes = vi.fn()
const getProjectDefaultModel = vi.fn()
const setProjectDefaultAgent = vi.fn()
const deactivateProjectAgent = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  // ApiError / isEndpointMissing stay REAL: "the backend is not deployed here"
  // is decided by an HTTP status, and a stubbed decision would pass while the
  // real one is broken.
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
    listAgentTypes: (...a: unknown[]) => listAgentTypes(...a),
    getProjectDefaultModel: (...a: unknown[]) => getProjectDefaultModel(...a),
    setProjectDefaultAgent: (...a: unknown[]) => setProjectDefaultAgent(...a),
    deactivateProjectAgent: (...a: unknown[]) => deactivateProjectAgent(...a),
  }
})

import AgentTeamSettings from './AgentTeamSettings.vue'

import { ApiError } from '@/api'
import { setLocale } from '@/i18n'
import { clearPageCache } from '@/lib/pageCache'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

function agent(overrides: Partial<ProjectAgent> = {}): ProjectAgent {
  return {
    configuration: { body: '', skills: [] },
    id: 'a1',
    project_id: PROJECT,
    handle: 'cheese',
    type_name: null,
    display_name: '芝士',
    seat_handle: 'cheese-a1',
    is_default: true,
    is_active: true,
    ...overrides,
  }
}

function mountPage() {
  return render(AgentTeamSettings, {
    props: { projectId: PROJECT },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  // 页面缓存是模块级的、跨用例活着的：不清的话上一条用例的名册会被下一条用例的
  // 第一帧画出来（那正是「第二次进不转圈」的设计），断言就打在旧数据上。
  clearPageCache()
  listProjectAgents.mockReset()
  listAgentTypes.mockReset().mockResolvedValue({ data: [], total: 0 })
  getProjectDefaultModel.mockReset().mockResolvedValue({
    choices: [
      { id: 'glm-5.2', label: 'GLM-5.2', default: true, allowed: true, requires_plan: null, efforts: [] },
      { id: 'kimi-k3', label: 'Kimi K3', default: false, allowed: true, requires_plan: null, efforts: ['low', 'high'] },
    ],
  })
  setProjectDefaultAgent.mockReset()
  deactivateProjectAgent.mockReset()
})

afterEach(() => cleanup())

describe('队友名册', () => {
  it('每一行说清这个队友是谁、什么角色', async () => {
    listAgentTypes.mockResolvedValue({
      data: [
        { name: 'reviewer', title: '代码评审', description: '', body: '', skills: [], mcp_servers: [], builtin: false },
      ],
      total: 1,
    })
    listProjectAgents.mockResolvedValue({
      data: [
        agent({ id: 'a1', handle: 'cheese', display_name: '芝士' }),
        agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false, type_name: 'reviewer' }),
      ],
      total: 2,
    })
    mountPage()
    expect(await screen.findByText('@cheese · 通用')).toBeTruthy()
    expect(await screen.findByText('@reviewer · 代码评审')).toBeTruthy()
  })

  it('每一行写着模型和思考强度，跟随项目时写出项目那个模型', async () => {
    listProjectAgents.mockResolvedValue({
      data: [
        agent({ id: 'a1', handle: 'cheese' }),
        agent({
          id: 'a2',
          handle: 'reviewer',
          display_name: '评审',
          is_default: false,
          configuration: { body: '', skills: [], model: 'kimi-k3', effort: 'high' },
        }),
      ],
      total: 2,
    })
    mountPage()

    expect(await screen.findByText('模型：跟随项目（GLM-5.2）')).toBeTruthy()
    expect(await screen.findByText('思考：自动')).toBeTruthy()
    expect(await screen.findByText('模型：Kimi K3')).toBeTruthy()
    expect(await screen.findByText('思考：高')).toBeTruthy()
    expect(screen.queryByText(/记忆/)).toBeNull()
  })

  it('只有默认那一个不显示「设为默认」', async () => {
    listProjectAgents.mockResolvedValue({
      data: [agent({ id: 'a1' }), agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false })],
      total: 2,
    })
    setProjectDefaultAgent.mockResolvedValue(agent({ id: 'a2', is_default: true }))
    mountPage()

    const buttons = await screen.findAllByRole('button', { name: '设为默认' })
    expect(buttons).toHaveLength(1)
    await fireEvent.click(buttons[0])
    await waitFor(() => {
      expect(setProjectDefaultAgent).toHaveBeenCalledWith(PROJECT, { instance_id: 'a2' })
    })
  })
})

describe('还没有队友的时候', () => {
  it('空状态说没有队友，并给出新建的入口', async () => {
    listProjectAgents.mockResolvedValue({ data: [], total: 0 })
    mountPage()

    expect(await screen.findByText('暂无 AI 队友')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /新建队友/ }).length).toBeGreaterThan(0)
  })
})

describe('后端还没上线', () => {
  it('说明功能未上线，而不是白屏或一句像 bug 的报错', async () => {
    listProjectAgents.mockRejectedValue(new ApiError(404, 'Not Found'))
    mountPage()

    expect(await screen.findByText('暂不支持管理 AI 队友')).toBeTruthy()
    // 不是空状态：说「暂无队友」等于宣布这个项目没有 AI 在干活，那是假话。
    expect(screen.queryByText('暂无 AI 队友')).toBeNull()
    expect(screen.queryByText('Not Found')).toBeNull()
  })

  it('真的坏了（不是 404）时照常报错', async () => {
    listProjectAgents.mockRejectedValue(new ApiError(500, '服务出错了'))
    mountPage()

    expect(await screen.findByText('服务出错了')).toBeTruthy()
    expect(screen.queryByText(/暂不支持/)).toBeNull()
  })
})

describe('停用', () => {
  it('先问一遍，并说明已经在用的话题不受影响', async () => {
    listProjectAgents.mockResolvedValue({
      data: [agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false })],
      total: 1,
    })
    deactivateProjectAgent.mockResolvedValue({ deleted: true })
    mountPage()

    await fireEvent.click(await screen.findByRole('button', { name: '停用' }))
    expect(await screen.findByText('停用「评审」')).toBeTruthy()
    expect(screen.getByText(/已在用它的话题照常工作/)).toBeTruthy()
    expect(deactivateProjectAgent).not.toHaveBeenCalled()
  })

  it('取消就什么都不做', async () => {
    listProjectAgents.mockResolvedValue({
      data: [agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false })],
      total: 1,
    })
    mountPage()

    await fireEvent.click(await screen.findByRole('button', { name: '停用' }))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))
    expect(deactivateProjectAgent).not.toHaveBeenCalled()
  })

  it('已停用的还列在名册上，标出来，并且不再给「停用」和「设为默认」', async () => {
    // 管理页要能看到它们 —— 一个停用的队友还在，它只是不接新活了。
    listProjectAgents.mockResolvedValue({
      data: [agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false, is_active: false })],
      total: 1,
    })
    mountPage()

    expect(await screen.findByText('评审')).toBeTruthy()
    expect(screen.getByText('已停用')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '停用' })).toBeNull()
    expect(screen.queryByRole('button', { name: '设为默认' })).toBeNull()
  })

  it('确认之后才真的停用', async () => {
    listProjectAgents.mockResolvedValue({
      data: [agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false })],
      total: 1,
    })
    deactivateProjectAgent.mockResolvedValue({ deleted: true })
    mountPage()

    await fireEvent.click(await screen.findByRole('button', { name: '停用' }))
    const dialog = await screen.findByRole('dialog')
    const confirm = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent?.trim() === '停用')
    await fireEvent.click(confirm as HTMLButtonElement)
    await waitFor(() => {
      expect(deactivateProjectAgent).toHaveBeenCalledWith(PROJECT, 'a2')
    })
  })
})
