import { describe, expect, it } from 'vitest'

import { parseApprovalsInput, parseCheckPaths } from './branchProtection'

describe('parseCheckPaths', () => {
  it('按逗号和空白切分，去掉空串和重复', () => {
    expect(parseCheckPaths(' backend/** , docs/*.md backend/**')).toEqual(['backend/**', 'docs/*.md'])
  })

  it('中文逗号也是分隔符', () => {
    expect(parseCheckPaths('backend/**，frontend/**')).toEqual(['backend/**', 'frontend/**'])
  })

  it('空输入 = 不限路径', () => {
    expect(parseCheckPaths('')).toEqual([])
    expect(parseCheckPaths('  ,  ，  ')).toEqual([])
  })
})

describe('parseApprovalsInput', () => {
  it('接受不小于 1 的整数，容忍两侧空白', () => {
    expect(parseApprovalsInput('1')).toBe(1)
    expect(parseApprovalsInput(' 3 ')).toBe(3)
  })

  it('拒绝 0、负数、小数和非数字', () => {
    for (const bad of ['0', '-1', '1.5', 'abc', '', '1e2']) {
      expect(parseApprovalsInput(bad), bad).toBeNull()
    }
  })
})
