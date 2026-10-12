import { describe, expect, it } from 'vitest'

import { checkVerdictRank, FAILED_CONCLUSIONS, isFailed, isNoVerdict, NO_VERDICT_CONCLUSIONS } from './checkVerdict'

describe('一条 check run 的结论', () => {
  it('被取消的不是「未通过」——PR #3463 的误报就是把它读成了红', () => {
    expect(isFailed({ status: 'completed', conclusion: 'cancelled' })).toBe(false)
    expect(isFailed({ status: 'completed', conclusion: 'stale' })).toBe(false)
  })

  it('被取消的也不是通过：它没有结论，卡面得照实说出来', () => {
    expect(isNoVerdict({ status: 'completed', conclusion: 'cancelled' })).toBe(true)
    expect(isNoVerdict({ status: 'completed', conclusion: 'stale' })).toBe(true)
  })

  it('真红的照旧算红', () => {
    for (const conclusion of ['failure', 'timed_out', 'action_required', 'startup_failure']) {
      expect(isFailed({ status: 'completed', conclusion })).toBe(true)
      expect(isNoVerdict({ status: 'completed', conclusion })).toBe(false)
    }
  })

  it('绿的两个都不是', () => {
    for (const conclusion of ['success', 'neutral', 'skipped']) {
      expect(isFailed({ status: 'completed', conclusion })).toBe(false)
      expect(isNoVerdict({ status: 'completed', conclusion })).toBe(false)
    }
  })

  it('还在跑的不算红，也没有「已取消」可说', () => {
    expect(isFailed({ status: 'in_progress', conclusion: null })).toBe(false)
    expect(isNoVerdict({ status: 'in_progress', conclusion: null })).toBe(false)
  })

  it('缺字段（接口给了个空行）不会炸，也不冤判成红', () => {
    expect(isFailed({})).toBe(false)
    expect(isNoVerdict({ conclusion: null })).toBe(false)
    expect(isFailed({ conclusion: undefined })).toBe(false)
  })

  it('两套词表不重叠：一条检查不可能既是红的又没有结论', () => {
    for (const conclusion of FAILED_CONCLUSIONS) {
      expect(NO_VERDICT_CONCLUSIONS).not.toContain(conclusion)
    }
  })
})

describe('展开明细的排序', () => {
  it('红的在最前，其次是还在跑的，再次是已取消的，绿的垫底', () => {
    const rows = [
      { status: 'completed', conclusion: 'success' },
      { status: 'completed', conclusion: 'cancelled' },
      { status: 'completed', conclusion: 'failure' },
      { status: 'in_progress', conclusion: null },
    ]

    const ordered = [...rows].sort((a, b) => checkVerdictRank(a) - checkVerdictRank(b))

    expect(ordered[0].conclusion).toBe('failure')
    expect(ordered[1].status).toBe('in_progress')
    expect(ordered[2].conclusion).toBe('cancelled')
    expect(ordered[3].conclusion).toBe('success')
  })

  it('已取消的不插进红的里面冒充红的', () => {
    const cancelled = { status: 'completed', conclusion: 'cancelled' }
    const failure = { status: 'completed', conclusion: 'failure' }

    expect(checkVerdictRank(cancelled)).toBeGreaterThan(checkVerdictRank(failure))
  })
})
