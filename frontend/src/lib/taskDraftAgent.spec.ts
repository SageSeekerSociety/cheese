import { describe, expect, it } from 'vitest'

import { missingOf, previewOf } from './taskDraftAgent'

const terms = {
  categories: [{ id: 3, name: '实验' }],
  topics: [
    { id: 8, name: '存储器层次结构' },
    { id: 9, name: 'C 语言' },
  ],
}

describe('previewOf', () => {
  it('turns what Cheese writes into the form fields it can be sure of', () => {
    const shown = previewOf(
      {
        draft: 41,
        name: 'Lab 3 缓存模拟器',
        difficulty: '中级',
        form: '团队',
        category: '实验',
        tags: ['C 语言', '不存在'],
        submission: [{ name: 'csim.c', kind: '文件' }],
        rubric: [{ item: '正确性', points: 60 }],
        participant_limit: 0,
      },
      terms
    )
    expect(shown.fields).toEqual({
      name: 'Lab 3 缓存模拟器',
      rank: 2,
      submitterType: 'TEAM',
      categoryId: 3,
      topics: [9],
      submissionSchema: [{ prompt: 'csim.c', type: 'FILE' }],
      rubric: [{ item: '正确性', points: 60, criteria: '' }],
      participantLimit: null,
    })
    expect(shown.writing).toBe('participant_limit')
  })

  it('leaves out what is not finished or not in the space', () => {
    const shown = previewOf({ name: 'Lab', deadline: '2026-11-0', category: '作业', difficulty: '中' }, terms)
    expect(shown.fields).toEqual({ name: 'Lab' })
    expect(shown.writing).toBe('difficulty')
  })

  it('reads a whole deadline on the person’s clock and the description as Markdown', () => {
    const shown = previewOf({ deadline: '2026-11-08 23:59', description: '## 要求' }, terms)
    expect(new Date(shown.fields.deadline as number).getMinutes()).toBe(59)
    expect(shown.fields.descriptionMarkdown).toBe('## 要求')
  })
})

describe('previewOf：只改详情里的一段', () => {
  const terms = { categories: [], topics: [] }
  const markdown = '用 C 写一个缓存模拟器。\n\n替换策略为 LRU。'

  it('原文写完、新文字还没开始：详情不动，原文是选中的那段', () => {
    const shown = previewOf({ description_edits: [{ find: '替换策略为 LRU' }] }, terms, markdown)
    expect(shown.fields.description).toBeUndefined()
    expect(shown.passage).toEqual({ find: '替换策略为 LRU', replace: null })
    expect(shown.writing).toBe('description')
  })

  it('新文字写到哪换到哪', () => {
    const shown = previewOf(
      { description_edits: [{ find: '替换策略为 LRU', replace: '替换策略为 LRU，并统' }] },
      terms,
      markdown
    )
    expect(shown.fields.descriptionMarkdown).toBe('用 C 写一个缓存模拟器。\n\n替换策略为 LRU，并统。')
    expect(shown.passage?.replace).toBe('替换策略为 LRU，并统')
  })

  it('原文还没写完、或详情里找不到：先不动', () => {
    expect(previewOf({ description_edits: [{ find: '替换策' }] }, terms, '别的内容').passage).toBeUndefined()
  })
})

describe('missingOf', () => {
  it('和后端同一条规矩：名称、详情、参与方式、难度', () => {
    expect(missingOf({})).toEqual(['name', 'description', 'form', 'difficulty'])
    expect(
      missingOf({ name: 'Lab 3', descriptionMarkdown: '写一个缓存模拟器', submitterType: 'USER', rank: 2 })
    ).toEqual([])
    const doc = JSON.stringify({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: '要求' }] }],
    })
    expect(missingOf({ name: 'x', description: doc, submitterType: 'TEAM', rank: 1 })).toEqual([])
  })
})
