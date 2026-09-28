// 接口题目 → 界面题目这一层的映射。单独钉它，是因为**摘出处**这件事只有这里有：
// 卡片、详情、搜索读的都是映射之后的 `summary`，而接口给的 `intro` 里带着一段给机器
// 认的前缀。映射写错，同一串字会在卡片上出现两次（一次当标、一次当正文），或者那串
// 给机器看的字直接漏给用户。
import type { Task } from '@/types'

import { describe, expect, it } from 'vitest'

import { toBoardTask } from './store'

/** 一道题。真 `Task` 有几十格，这里给的是 `toBoardTask` 真读的那几格。 */
function task(over: Partial<Task> = {}): Task {
  const base = {
    id: 1,
    name: '题目',
    intro: '题干',
    creator: { id: 9, username: 'alice', nickname: 'Alice' },
    approved: 'APPROVED',
    createdAt: 1_700_000_000_000,
    publishedAt: 1_700_000_000_000,
    deadline: null,
    participantLimit: 0,
    minTeamSize: 1,
    maxTeamSize: 1,
    videoUrl: null,
    participants: { total: 3, examples: [] },
  } as unknown as Task
  return { ...base, ...over }
}

describe('题目映射', () => {
  it('从 PDF 来的题：出处进 origin，正文里那串字被摘掉', () => {
    const mapped = toBoardTask(task({ intro: '【PDF · 第 3 页】实现一个缓存' }))

    expect(mapped.origin).toBe('PDF · 第 3 页')
    expect(mapped.summary).toBe('实现一个缓存')
    // 「不重复出现」是这条的要点：正文里一个 PDF 字样都不该剩。
    expect(mapped.summary).not.toContain('PDF')
  })

  it('手写的题：没有 origin，正文一个字符都不动', () => {
    const mapped = toBoardTask(task({ intro: '实现一个缓存' }))

    expect(mapped.origin).toBeUndefined()
    expect(mapped.summary).toBe('实现一个缓存')
  })

  it('领取数照旧是 participants.total（它是领取**次数**，不是人数）', () => {
    const mapped = toBoardTask(task({ participants: { total: 5, examples: [] } }))

    expect(mapped.claimCount).toBe(5)
  })

  it('题目别的格子照旧从真字段来（摘出处没顺手改别的东西）', () => {
    const mapped = toBoardTask(
      task({
        id: 42,
        name: '缓存',
        category: { id: 1, name: '系统' } as Task['category'],
        deadline: 1_800_000_000_000,
        participantLimit: 0,
      })
    )

    expect(mapped.id).toBe('42')
    expect(mapped.title).toBe('缓存')
    expect(mapped.category).toBe('系统')
    expect(mapped.publisher).toEqual({ handle: 'alice', name: 'Alice' })
    // 真库用 0 表示不限，界面用 null。
    expect(mapped.participantLimit).toBeNull()
  })
})
