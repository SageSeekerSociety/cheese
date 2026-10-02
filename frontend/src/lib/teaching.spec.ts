// 「给 AI 队友的指导」在表单与接口之间的翻译（#944）。整份替换的语义全压在这一对
// 转换上：空的那几格要落成 `null` / `[]`（与 `Teaching.is_empty` 同一个意思），
// 否则「留空」会被读成「写了些空的东西」，下一层就被盖住了。
import { describe, expect, it } from 'vitest'

import { buildTeaching, draftFromConfig, emptyTeachingDraft, isTeachingBlank, splitIds, splitList } from './teaching'

describe('teaching 表单转换', () => {
  it('接口那一份读成六格：清单拼成逗号串，没设的落成空串', () => {
    expect(
      draftFromConfig({
        systemPrompt: '第 {current_week} 周',
        currentWeek: 3,
        allowedTopics: ['链表', '栈'],
        avoidInCode: ['递归'],
        materialIds: [11, 12],
        knowledgeIds: [7],
      })
    ).toEqual({
      systemPrompt: '第 {current_week} 周',
      currentWeek: '3',
      allowedTopics: '链表, 栈',
      avoidInCode: '递归',
      materialIds: '11, 12',
      knowledgeIds: '7',
    })
    expect(draftFromConfig(null)).toEqual(emptyTeachingDraft())
  })

  it('六格写回接口那一份：空的是 null / []，不是空串', () => {
    expect(
      buildTeaching({
        systemPrompt: '  讲完链表了  ',
        currentWeek: '3',
        allowedTopics: '链表，栈, 队列',
        avoidInCode: '',
        materialIds: '11, 12',
        knowledgeIds: '',
      })
    ).toEqual({
      systemPrompt: '讲完链表了',
      currentWeek: 3,
      allowedTopics: ['链表', '栈', '队列'],
      avoidInCode: [],
      materialIds: [11, 12],
      knowledgeIds: [],
    })
  })

  it('周次留空是不设（null），不是 0', () => {
    const week = buildTeaching({ ...emptyTeachingDraft(), currentWeek: '  ' })
    expect(week.currentWeek).toBeNull()
  })

  it('清单认中英文逗号、去掉空项；编号只留正整数', () => {
    expect(splitList(' a，b , ,c ')).toEqual(['a', 'b', 'c'])
    expect(splitIds('11, -2, x, 0, 12')).toEqual([11, 12])
  })

  it('六格全空才是「没说」—— 任何一格有东西都不算空', () => {
    expect(isTeachingBlank(undefined)).toBe(true)
    expect(isTeachingBlank({})).toBe(true)
    expect(
      isTeachingBlank({
        systemPrompt: null,
        currentWeek: null,
        allowedTopics: [],
        avoidInCode: [],
        materialIds: [],
        knowledgeIds: [],
      })
    ).toBe(true)

    expect(isTeachingBlank({ currentWeek: 0 })).toBe(false)
    expect(isTeachingBlank({ allowedTopics: ['链表'] })).toBe(false)
    expect(isTeachingBlank({ systemPrompt: '嗨' })).toBe(false)
  })
})
