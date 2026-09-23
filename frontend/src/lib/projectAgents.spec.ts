// 这一页上的那个数字是它存在的理由（见 projectAgents.ts 的开头），所以它
// 单独被盯着：算错了不会渲染失败，只会安静地骗人 —— 一个攒了 25 条记忆的队友
// 显示成 0。
import type { MemoryEntryOut } from '../api'

import { describe, expect, it } from 'vitest'

import { displayNameError, handleError, memoryCountsByHandle, typeLabel } from './projectAgents'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'
const OTHER_PROJECT = '11111111-2222-3333-4444-555555555555'

function memory(scope: string, scopeId: string): MemoryEntryOut {
  return { id: `${scope}-${scopeId}-${Math.random()}`, scope, scope_id: scopeId, content: '一条', created_at: '' }
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

describe('显示', () => {
  const types = [
    {
      name: 'fullstack-engineer',
      title: '全栈工程',
      description: '',
      body: '',
      skills: [],
      mcp_servers: [],
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
