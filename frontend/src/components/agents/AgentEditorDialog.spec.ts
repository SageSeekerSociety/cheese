// 新建 / 修改队友的表单。两件事值得被盯着：
//   1. 名字空着、标识写错，不能一路发到后端再收一个 422 —— 人得当场看见
//   2. 只是改了个名字，不能顺手把一个被别的项目共用的类型也重写一遍
import type { AgentType, ProjectAgent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const createProjectAgent = vi.fn()
const updateProjectAgent = vi.fn()
const updateAgentType = vi.fn()
const createAgentType = vi.fn()
const setProjectDefaultAgent = vi.fn()

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    createProjectAgent: (...a: unknown[]) => createProjectAgent(...a),
    updateProjectAgent: (...a: unknown[]) => updateProjectAgent(...a),
    updateAgentType: (...a: unknown[]) => updateAgentType(...a),
    createAgentType: (...a: unknown[]) => createAgentType(...a),
    setProjectDefaultAgent: (...a: unknown[]) => setProjectDefaultAgent(...a),
  }
})

import AgentEditorDialog from './AgentEditorDialog.vue'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

const CUSTOM_TYPE: AgentType = {
  name: 'reviewer',
  title: '代码评审',
  description: '',
  body: '你负责代码评审',
  skills: [],
  mcp_servers: [],
  model: null,
  effort: null,
  harness: null,
  builtin: false,
}

function mountDialog(agent: ProjectAgent | null, types: AgentType[] = [CUSTOM_TYPE]) {
  return render(AgentEditorDialog, {
    props: { modelValue: true, projectId: PROJECT, agent, types },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

async function clickSave() {
  const dialog = await screen.findByRole('dialog')
  const save = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存')
  await fireEvent.click(save as HTMLButtonElement)
}

function field(label: string): HTMLInputElement {
  return screen.getByLabelText(label, { exact: false }) as HTMLInputElement
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
  createProjectAgent.mockReset().mockResolvedValue({})
  updateProjectAgent.mockReset().mockResolvedValue({})
  updateAgentType.mockReset().mockResolvedValue(CUSTOM_TYPE)
  createAgentType.mockReset()
  setProjectDefaultAgent.mockReset().mockResolvedValue({})
})

afterEach(() => cleanup())

describe('新建时的校验', () => {
  it('一进来不先骂人', async () => {
    mountDialog(null)
    expect(screen.queryByText('请填写名字')).toBeNull()
  })

  it('名字空着就不发请求，并当场说明', async () => {
    mountDialog(null)
    await clickSave()
    expect(await screen.findByText('请填写名字')).toBeTruthy()
    expect(createProjectAgent).not.toHaveBeenCalled()
  })

  it('标识写成大写或带空格会被拦下', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    await fireEvent.update(field('标识'), 'Code Reviewer')
    await clickSave()
    expect(await screen.findByText(/只能用小写字母、数字/)).toBeTruthy()
    expect(createProjectAgent).not.toHaveBeenCalled()
  })

  it('填对了就带着去掉首尾空格的值提交', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '  代码评审  ')
    await fireEvent.update(field('标识'), 'reviewer-2')
    await clickSave()
    await waitFor(() => {
      expect(createProjectAgent).toHaveBeenCalledWith(PROJECT, {
        display_name: '代码评审',
        handle: 'reviewer-2',
        type_name: null,
      })
    })
  })

  it('标识留空就不传，交给后端生成', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    await clickSave()
    await waitFor(() => {
      expect(createProjectAgent).toHaveBeenCalledWith(PROJECT, {
        display_name: '代码评审',
        handle: undefined,
        type_name: null,
      })
    })
  })
})

describe('修改时', () => {
  const existing: ProjectAgent = {
    id: 'a2',
    project_id: PROJECT,
    handle: 'reviewer',
    type_name: 'reviewer',
    display_name: '代码评审',
    is_default: false,
    configured: true,
  }

  it('只改名字时不去动那个可能被别处共用的类型', async () => {
    mountDialog(existing)
    await fireEvent.update(field('名字'), '严格评审')
    await clickSave()
    await waitFor(() => {
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, 'a2', {
        display_name: '严格评审',
        type_name: 'reviewer',
      })
    })
    expect(updateAgentType).not.toHaveBeenCalled()
  })

  it('改了角色设定才写回类型', async () => {
    mountDialog(existing)
    await fireEvent.update(field('角色设定'), '你负责代码评审，先看测试')
    await clickSave()
    await waitFor(() => {
      expect(updateAgentType).toHaveBeenCalledWith(
        'reviewer',
        expect.objectContaining({ body: '你负责代码评审，先看测试' })
      )
    })
  })

  // 一个从没配过队友的项目，名册里只有这一条。它没有 id，但它真的在干活、
  // 真的有记忆 —— 所以「给它换个类型」必须通，不能因为没 id 就变成死路。
  it('项目自带的那个队友改不了名字，但换类型是通的', async () => {
    const implicit: ProjectAgent = {
      id: null,
      project_id: PROJECT,
      handle: 'cheese',
      type_name: null,
      display_name: '芝士',
      is_default: true,
      configured: false,
    }
    mountDialog({ ...implicit, type_name: 'reviewer' })
    expect(field('名字').readOnly).toBe(true)
    await clickSave()

    // 走的是「按类型设默认队友」那条 —— 后端会顺手把这一行落下来，
    // 它一直在攒的那份记忆原样跟过去。
    await waitFor(() => {
      expect(setProjectDefaultAgent).toHaveBeenCalledWith(PROJECT, { type_name: 'reviewer' })
    })
    expect(updateProjectAgent).not.toHaveBeenCalled()
  })

  it('平台预设是只读的，改不了', async () => {
    const preset: AgentType = { ...CUSTOM_TYPE, name: 'fullstack-engineer', title: '全栈工程', builtin: true }
    mountDialog({ ...existing, type_name: 'fullstack-engineer' }, [preset])
    expect(await screen.findByText('平台预设不能改')).toBeTruthy()
    expect((field('角色设定') as unknown as HTMLTextAreaElement).readOnly).toBe(true)
  })
})
