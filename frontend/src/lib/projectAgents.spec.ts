// 这一页上的那个数字是它存在的理由（见 projectAgents.ts 的开头），所以它
// 单独被盯着：算错了不会渲染失败，只会安静地骗人 —— 一个攒了 25 条记忆的队友
// 显示成 0。
import type { MemoryEntryOut } from '../api'
import type { ProjectAgent } from '../cx_types'

import { describe, expect, it } from 'vitest'

import {
  agentKey,
  displayNameError,
  fieldChoices,
  fieldIsChoosable,
  handleError,
  memoryCountsByHandle,
  typeLabel,
} from './projectAgents'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const OTHER_PROJECT = '11111111-2222-3333-4444-555555555555'

function memory(scope: string, scopeId: string): MemoryEntryOut {
  return { id: `${scope}-${scopeId}-${Math.random()}`, scope, scope_id: scopeId, content: '一条', created_at: '' }
}

function agent(overrides: Partial<ProjectAgent> = {}): ProjectAgent {
  return {
    configuration: { body: '', model: 'sonnet', harness: 'claude-code', skills: [], mcp_servers: [], effort: null },
    id: 'a1',
    project_id: PROJECT,
    handle: 'cheese',
    type_name: null,
    display_name: '芝士',
    is_default: true,
    configured: true,
    is_active: true,
    ...overrides,
  }
}

describe('记忆条数', () => {
  it('按 handle 分开数，不同队友互不串味', () => {
    const counts = memoryCountsByHandle(
      [
        memory('agent_project', `${PROJECT}:cheese`),
        memory('agent_project', `${PROJECT}:cheese`),
        memory('agent_project', `${PROJECT}:reviewer`),
      ],
      PROJECT
    )
    expect(counts).toEqual({ cheese: 2, reviewer: 1 })
  })

  it('项目共享池和个人记忆不算在任何队友头上', () => {
    const counts = memoryCountsByHandle(
      [memory('project', PROJECT), memory('user', 'wangchangxin'), memory('agent_project', `${PROJECT}:cheese`)],
      PROJECT
    )
    expect(counts).toEqual({ cheese: 1 })
  })

  it('别的项目的池不算进来', () => {
    const counts = memoryCountsByHandle([memory('agent_project', `${OTHER_PROJECT}:cheese`)], PROJECT)
    expect(counts).toEqual({})
  })
})

describe('列表键', () => {
  it('项目还没配过队友时那条隐式的芝士，不会和别人撞键', () => {
    const implicit = agent({ id: null, configured: false })
    expect(agentKey(implicit)).not.toBe(agentKey(agent({ id: 'a1' })))
  })
})

describe('显示', () => {
  const types = [
    {
      name: 'fullstack-engineer',
      title: '全栈工程',
      description: '',
      body: '',
      skills: [],
      mcp_servers: [],
      model: null,
      effort: null,
      harness: null,
      builtin: true,
    },
  ]

  it('没指定类型时显示「通用」', () => {
    expect(typeLabel(types, null)).toBe('通用')
  })

  it('类型已经不在目录里时，退回显示它的名字而不是「通用」', () => {
    expect(typeLabel(types, 'deleted-one')).toBe('deleted-one')
  })
})

describe('表单校验', () => {
  it('名字不能是空白', () => {
    expect(displayNameError('   ')).toBeTruthy()
    expect(displayNameError('评审')).toBeNull()
  })

  it('名字最长 64 个字', () => {
    expect(displayNameError('字'.repeat(64))).toBeNull()
    expect(displayNameError('字'.repeat(65))).toBeTruthy()
  })

  it('标识留空是允许的 —— 交给后端按名字生成', () => {
    expect(handleError('')).toBeNull()
  })

  it('标识不接受大写、空格和会切坏记忆池键的冒号', () => {
    expect(handleError('Reviewer')).toBeTruthy()
    expect(handleError('code reviewer')).toBeTruthy()
    expect(handleError('a:b')).toBeTruthy()
    expect(handleError('code-reviewer.2')).toBeNull()
  })
})

describe('编辑器能提供什么设置', () => {
  const OPTS = {
    model: {
      state: 'choosable',
      choices: [
        { id: 'sonnet', label: 'Sonnet 5', description: '均衡', default: true },
        { id: 'opus', label: 'Opus 5', description: '最强', default: false },
      ],
      reason: '',
      note: '',
    },
    effort: { state: 'unavailable', choices: [], reason: 'noConsumerOnRunPath', note: '模型自己决定' },
  }

  it('可选字段交出的就是后端那份清单，一个不多一个不少', () => {
    // 前端自带清单时的两种坏法：多出来的值发到后端被 422，少掉的值人永远选不到。
    expect(fieldChoices(OPTS, 'model').map((c) => c.id)).toEqual(['sonnet', 'opus'])
    expect(fieldIsChoosable(OPTS, 'model')).toBe(true)
  })

  it('目录取不到时一个选项都不交', () => {
    // 目录没取到 ≠ 平台限制：界面上就不渲染这一格，而不是渲染一个空下拉。
    expect(fieldChoices({}, 'model')).toEqual([])
    expect(fieldIsChoosable({}, 'model')).toBe(false)
  })

  it('不可选字段一个选项都不交，即使它带着残留的 choices', () => {
    // state 是唯一判据。哪天目录里一个 unavailable 字段带回了 choices，它也不能
    // 被渲染成能选 —— 那正是「填了不生效」重新长回来的路径。
    const stale = {
      x: {
        state: 'unavailable',
        choices: [{ id: 'a', label: 'A', description: '', default: false }],
        reason: 'r',
        note: 'n',
      },
    }
    expect(fieldChoices(stale, 'x')).toEqual([])
    expect(fieldIsChoosable(stale, 'x')).toBe(false)
  })
})
