// 「AI 队友」管理页。四件事值得被盯着，都是渲染不会失败但人会被误导的：
//   1. 一行里那两个数字要真的对上这个队友（记忆条数、几个话题在用）
//   2. 后端那一半还没上线时，这一页得说「还没上线」，不能是白屏也不能是报错
//   3. 空名册要说清楚队友是什么、能拿它干嘛，不能只画个空盒子
//   4. 停用必须先问一遍，并且说明「已经在用的话题照常工作、记忆保留」
import type { ProjectAgent } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectAgents = vi.fn()
const listAgentTypes = vi.fn()
const listMemory = vi.fn()
const listTopics = vi.fn()
const setProjectDefaultAgent = vi.fn()
const deactivateProjectAgent = vi.fn()

vi.mock('../api', async (importOriginal) => {
  // ApiError / isEndpointMissing stay REAL: "the backend is not deployed here"
  // is decided by an HTTP status, and a stubbed decision would pass while the
  // real one is broken.
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    listProjectAgents: (...a: unknown[]) => listProjectAgents(...a),
    listAgentTypes: (...a: unknown[]) => listAgentTypes(...a),
    listMemory: (...a: unknown[]) => listMemory(...a),
    listTopics: (...a: unknown[]) => listTopics(...a),
    setProjectDefaultAgent: (...a: unknown[]) => setProjectDefaultAgent(...a),
    deactivateProjectAgent: (...a: unknown[]) => deactivateProjectAgent(...a),
  }
})

import { ApiError } from '../api'

import ProjectAgentsView from './ProjectAgentsView.vue'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

function agent(overrides: Partial<ProjectAgent> = {}): ProjectAgent {
  return {
    id: 'a1',
    project_id: PROJECT,
    handle: 'cheese',
    type_name: null,
    display_name: '芝士',
    is_default: true,
    configured: true,
    ...overrides,
  }
}

function mountPage() {
  return render(ProjectAgentsView, {
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
  listProjectAgents.mockReset()
  listAgentTypes.mockReset().mockResolvedValue({ data: [], total: 0 })
  listMemory.mockReset().mockResolvedValue({ data: [], total: 0 })
  listTopics.mockReset().mockResolvedValue({ data: [], total: 0 })
  setProjectDefaultAgent.mockReset()
  deactivateProjectAgent.mockReset()
})

afterEach(() => cleanup())

describe('队友名册', () => {
  it('每一行带上这个队友自己的记忆条数和在用话题数', async () => {
    listProjectAgents.mockResolvedValue({
      data: [
        agent({ id: 'a1', handle: 'cheese' }),
        agent({ id: 'a2', handle: 'reviewer', display_name: '评审', is_default: false }),
      ],
      total: 2,
    })
    listMemory.mockResolvedValue({
      data: [
        {
          id: 'm1',
          scope: 'agent_project',
          scope_id: `${PROJECT}:cheese`,
          content: '沙箱里跑 jj 会打停全项目',
          created_at: '',
        },
        { id: 'm2', scope: 'agent_project', scope_id: `${PROJECT}:cheese`, content: '闸门只跑 lint', created_at: '' },
        { id: 'm3', scope: 'project', scope_id: PROJECT, content: '这条属于项目，不属于任何队友', created_at: '' },
      ],
      total: 3,
    })
    listTopics.mockResolvedValue({
      data: [
        { id: 't1', project_id: PROJECT, parent_id: null, title: 'A', kind: 'root', status: 'active', created_at: '' },
        {
          id: 't2',
          project_id: PROJECT,
          parent_id: null,
          title: 'B',
          kind: 'root',
          status: 'active',
          created_at: '',
          agent_instance_id: 'a2',
        },
      ],
      total: 2,
    })
    mountPage()

    expect(await screen.findByText('芝士')).toBeTruthy()
    // 芝士 owns two facts; the project-pool one is nobody's.
    expect(await screen.findByText('2 条记忆')).toBeTruthy()
    expect(await screen.findByText('0 条记忆')).toBeTruthy()
    // 默认那一个接手了没自己选队友的话题。
    const inUse = await screen.findAllByText('1 个话题在用')
    expect(inUse).toHaveLength(2)
  })

  it('点开记忆能看到这个队友学到的东西', async () => {
    listProjectAgents.mockResolvedValue({ data: [agent()], total: 1 })
    listMemory.mockResolvedValue({
      data: [
        { id: 'm1', scope: 'agent_project', scope_id: `${PROJECT}:cheese`, content: '闸门只跑 lint', created_at: '' },
      ],
      total: 1,
    })
    mountPage()

    const toggle = await screen.findByText('1 条记忆')
    expect(screen.queryByText('闸门只跑 lint')).toBeNull()
    await fireEvent.click(toggle)
    expect(await screen.findByText('闸门只跑 lint')).toBeTruthy()
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
  it('空状态说清楚队友是什么、能拿它干嘛', async () => {
    listProjectAgents.mockResolvedValue({ data: [], total: 0 })
    mountPage()

    expect(await screen.findByText('暂无 AI 队友')).toBeTruthy()
    expect(screen.getByText(/在这个项目里学到的东西会一直跟着它/)).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /新建队友/ }).length).toBeGreaterThan(0)
  })
})

describe('后端还没上线', () => {
  it('说明功能未上线，而不是白屏或一句像 bug 的报错', async () => {
    listProjectAgents.mockRejectedValue(new ApiError(404, 'Not Found'))
    mountPage()

    expect(await screen.findByText(/这个环境还没上线 AI 队友的管理功能/)).toBeTruthy()
    // 不是空状态：说「暂无队友」等于宣布这个项目没有 AI 在干活，那是假话。
    expect(screen.queryByText('暂无 AI 队友')).toBeNull()
    expect(screen.queryByText('Not Found')).toBeNull()
  })

  it('真的坏了（不是 404）时照常报错', async () => {
    listProjectAgents.mockRejectedValue(new ApiError(500, '服务出错了'))
    mountPage()

    expect(await screen.findByText('服务出错了')).toBeTruthy()
    expect(screen.queryByText(/还没上线/)).toBeNull()
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
    expect(await screen.findByText('停用队友')).toBeTruthy()
    expect(screen.getByText(/已经在用它的话题照常工作，它的记忆也都保留/)).toBeTruthy()
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
