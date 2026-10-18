// 这三个导出在仓库里没有任何调用点，已经删掉；它们的类型和调用点都没留。
// 没有这条断言，谁把其中任何一个贴回去都有一个绿着通过的 PR 可以走。
import { describe, expect, it } from 'vitest'

import * as zodErrorMap from '../lib/zodErrorMap'

import * as insights from './insights'

describe('已清退的死导出', () => {
  it('media 类型守卫不再导出', () => {
    expect('isInsightMediaImage' in insights).toBe(false)
    expect('isInsightMediaVideo' in insights).toBe(false)
  })

  it('zod 错误映射的测试重置入口不再导出', () => {
    expect('resetZodErrorMap' in zodErrorMap).toBe(false)
  })
})
