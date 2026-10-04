// 队友设置页上不放进组件里的那几样：显示名、表单校验、思考强度的叫法。
import { beforeEach, describe, expect, it } from 'vitest'

import { setLocale } from '../i18n'

import { displayNameError, effortLabel, handleError, typeLabel } from './projectAgents'

beforeEach(() => setLocale('zh-CN'))

describe('思考强度', () => {
  it('没选就是自动，选了就是那一档', () => {
    expect(effortLabel(null)).toBe('自动')
    expect(effortLabel('medium')).toBe('中')
    expect(effortLabel('max')).toBe('极高')
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
