// 这一页上的两个数字是它存在的理由（见 projectAgents.ts 的开头），所以它们
// 单独被盯着：算错了不会渲染失败，只会安静地骗人 —— 一个建完没人用的空壳显示
// 成「3 个话题在用」，或者一个攒了 25 条记忆的队友显示成 0。
import type { MemoryEntryOut } from '../api'
import type { ProjectAgent, Topic } from '../cx_types'

import { describe, expect, it } from 'vitest'

import {
  agentKey,
  displayNameError,
  effortLabel,
  handleError,
  memoryCountsByHandle,
  topicCountsByAgent,
  typeLabel,
} from './projectAgents'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const OTHER_PROJECT = '11111111-2222-3333-4444-555555555555'

function memory(scope: string, scopeId: string): MemoryEntryOut {
  return { id: `${scope}-${scopeId}-${Math.random()}`, scope, scope_id: scopeId, content: '一条', created_at: '' }
}

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

function topic(overrides: Partial<Topic> = {}): Topic {
  return {
    id: 't1',
    project_id: PROJECT,
    parent_id: null,
    title: '话题',
    kind: 'root',
    status: 'active',
    created_at: '',
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

describe('几个话题在用', () => {
  it('没自己选队友的话题算在默认那一个头上', () => {
    const agents = [agent({ id: 'a1', is_default: true }), agent({ id: 'a2', handle: 'reviewer', is_default: false })]
    const counts = topicCountsByAgent([topic({ id: 't1' }), topic({ id: 't2', agent_instance_id: 'a2' })], agents)
    expect(counts).toEqual({ a1: 1, a2: 1 })
  })

  it('已归档的话题不算 —— 问的是现在谁在用', () => {
    const agents = [agent({ id: 'a1', is_default: true })]
    const counts = topicCountsByAgent([topic({ id: 't1' }), topic({ id: 't2', archived_at: '2026-08-01' })], agents)
    expect(counts).toEqual({ a1: 1 })
  })

  it('一个队友都没被用到时是 0，不是缺项', () => {
    const agents = [agent({ id: 'a1', is_default: false })]
    expect(topicCountsByAgent([], agents)).toEqual({ a1: 0 })
  })

  it('话题指向一个已经不在名册上的队友时，不记到任何人头上', () => {
    const agents = [agent({ id: 'a1', is_default: false })]
    expect(topicCountsByAgent([topic({ agent_instance_id: 'gone' })], agents)).toEqual({ a1: 0 })
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

  it('认不出来的思考深度原样显示，不吞掉', () => {
    expect(effortLabel('high')).toBe('深')
    expect(effortLabel('unheard-of')).toBe('unheard-of')
    expect(effortLabel(null)).toBe('')
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
