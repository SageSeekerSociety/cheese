import type { MergeStateInfo, MergeStateWord, MergeWho } from '../cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { mergeBadgeOf, visibleReasons } from './mergeState'

import { setLocale } from '@/i18n'

// 这几个词现在走词表了，断言的是中文；happy-dom 的 navigator.language 是 en-US，
// 不钉住语言，渲染出来的就是英文。
beforeEach(() => setLocale('zh-CN'))

function ms(state: MergeStateWord, who: MergeWho, over: Partial<MergeStateInfo> = {}): MergeStateInfo {
  return {
    state,
    who,
    reasons: [{ kind: 'github_verdict', checks: [], detail: 'GitHub 的裁决' }],
    head_sha: 'abc1234',
    checked_at: '2026-09-06T00:00:00Z',
    since: null,
    ...over,
  }
}

// issue #718「卡上的小圈：谁的活」那张表，逐行。词和圈都只是翻译 —— state 和
// who 是后端算的，这里断言的是翻出来的字不走样。
describe('mergeBadgeOf', () => {
  it('clean → 可以合并，圈是「等你」', () => {
    expect(mergeBadgeOf(ms('clean', 'human'))).toEqual({ label: '可以合并', column: 'needs_you' })
  })

  it('dirty → 芝士处理中，无论 who 说什么（平台 lane 的冲突卡 who 恒 human）', () => {
    expect(mergeBadgeOf(ms('dirty', 'agent'))).toEqual({ label: '芝士处理中', column: 'building' })
    expect(mergeBadgeOf(ms('dirty', 'human'))).toEqual({ label: '芝士处理中', column: 'building' })
  })

  it('behind → 平台更新分支', () => {
    expect(mergeBadgeOf(ms('behind', 'platform'))).toEqual({ label: '平台更新分支', column: 'delivering' })
  })

  it('unstable / blocked 检查红了 → 芝士处理中', () => {
    expect(mergeBadgeOf(ms('unstable', 'agent'))?.label).toBe('芝士处理中')
    expect(mergeBadgeOf(ms('blocked', 'agent'))?.label).toBe('芝士处理中')
  })

  it('blocked 必跑检查没报到 / CI 在跑 → 等 CI', () => {
    expect(mergeBadgeOf(ms('blocked', 'ci'))).toEqual({ label: '等 CI', column: 'delivering' })
    expect(mergeBadgeOf(ms('unstable', 'ci'))?.label).toBe('等 CI')
  })

  it('blocked 采纳被新提交作废 → 等采纳，圈是「等你」', () => {
    expect(mergeBadgeOf(ms('blocked', 'human'))).toEqual({ label: '等采纳', column: 'needs_you' })
  })

  it('unknown 是「还没看过」，中性展示（平台 lane 恒是 clean/dirty，#363 拍板，走不到这档）', () => {
    expect(mergeBadgeOf(ms('unknown', 'platform'))).toEqual({ label: '状态更新中', column: 'delivering' })
  })
})

describe('visibleReasons', () => {
  it('滤掉只是在复述状态词的两类，其余保留', () => {
    const info = ms('blocked', 'agent', {
      reasons: [
        { kind: 'no_obstacle', checks: [], detail: '可以合并' },
        { kind: 'no_signal', checks: [], detail: '平台还没看过这个 PR 的合并态' },
        { kind: 'required_check_failed', checks: ['test'], detail: '必跑检查未通过' },
      ],
    })
    expect(visibleReasons(info)).toEqual([
      { kind: 'required_check_failed', checks: ['test'], detail: '必跑检查未通过' },
    ])
  })
})
