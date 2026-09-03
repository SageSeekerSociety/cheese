// 编辑器只提供后端说能提供的东西。
//
// 这一页最容易出的错不是渲染崩掉，是渲染得太好看：一个填了也不会生效的输入框，
// 人填完保存、界面一切正常、agent 照旧那样跑。所以这里盯的三件事都是「不该出现
// 的东西没有出现」——
//   1. 后端说某个字段不可选时，它不能变成输入框
//   2. 也不给它留一句「暂不可设置」的说明 —— 那还是在为不存在的功能留位置
//   3. 目录取不到时同样什么都不渲染 —— 一次请求失败不能被说成产品限制
import type { AgentType, ProjectAgent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAgentTypeOptions = vi.fn()

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, getAgentTypeOptions: () => getAgentTypeOptions() }
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

const AGENT: ProjectAgent = {
  id: 'a1',
  project_id: PROJECT,
  handle: 'reviewer',
  type_name: 'reviewer',
  display_name: '评审员',
  is_default: false,
  configured: true,
}

const OPTIONS = {
  model: {
    state: 'choosable' as const,
    choices: [
      { id: 'sonnet', label: 'Sonnet 5', description: '均衡', default: true },
      { id: 'opus', label: 'Opus 5', description: '最强', default: false },
    ],
    reason: '',
    note: '',
  },
  effort: {
    state: 'unavailable' as const,
    choices: [],
    reason: 'noConsumerOnRunPath',
    note: '模型自己决定思考深度，平台设了也不会生效',
  },
  mcp_servers: {
    state: 'unavailable' as const,
    choices: [],
    reason: 'noRegistryYet',
    note: '还没有可选的 MCP 服务目录',
  },
}

function mountDialog() {
  return render(AgentEditorDialog, {
    props: { modelValue: true, projectId: PROJECT, agent: AGENT, types: [CUSTOM_TYPE] },
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
      value: {
        width: 1024,
        height: 768,
        offsetLeft: 0,
        offsetTop: 0,
        addEventListener() {},
        removeEventListener() {},
      },
    })
  }
})

beforeEach(() => {
  getAgentTypeOptions.mockReset().mockResolvedValue(OPTIONS)
})

afterEach(cleanup)

describe('只提供真的会生效的设置', () => {
  it('后端说某个字段不可选，就不给它输入框', async () => {
    mountDialog()
    await waitFor(() => expect(getAgentTypeOptions).toHaveBeenCalled())
    // 「思考深度」和「外部工具」在这一版都没有消费方 —— 它们的旧输入框曾经在
    // 这里，填了完全不生效，是这次改动要消灭的东西。
    await waitFor(() => expect(screen.queryByLabelText('思考深度')).toBeNull())
    expect(screen.queryByLabelText('外部工具')).toBeNull()
  })

  it('也不给它留一句说明 —— 界面上不为不存在的功能留位置', async () => {
    mountDialog()
    await waitFor(() => expect(getAgentTypeOptions).toHaveBeenCalled())
    // 「暂不可设置：思考深度 —— …」这种说明曾经在这里。它比空输入框好不了多少：
    // 人照样会去理解一个还不存在的功能，然后等它。理由留在后端目录里给代码读。
    expect(screen.queryByText(/暂不可设置/)).toBeNull()
    expect(screen.queryByText(/模型自己决定思考深度/)).toBeNull()
  })

  it('后端说模型可选，就给出模型这一格', async () => {
    mountDialog()
    // 选项本身由 lib/projectAgents.spec.ts 直接盯着（Vuetify 的浮层在 jsdom 里
    // 打不开，靠点开菜单去验证会把「测承诺」变成「测浮层」）。这里只保这一格
    // 出现，两边合起来才是完整的：有这一格，且格里就是后端那份清单。
    expect(await screen.findByLabelText('模型')).toBeTruthy()
  })

  it('目录取不到时，不渲染选择器也不宣布任何限制', async () => {
    getAgentTypeOptions.mockRejectedValue(new Error('boom'))
    mountDialog()
    await waitFor(() => expect(getAgentTypeOptions).toHaveBeenCalled())
    // 一次网络失败不该被人读成「这个功能被砍了」，所以也不解释，就是不渲染。
    expect(screen.queryByLabelText('模型')).toBeNull()
  })
})
