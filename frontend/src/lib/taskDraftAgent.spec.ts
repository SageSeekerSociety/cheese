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
      submissionSchema: [{ prompt: 'csim.c', type: 'FILE', stage: null }],
      rubric: [{ item: '正确性', points: 60, criteria: '', stage: null }],
      participantLimit: null,
    })
    expect(shown.writing).toBe('participant_limit')
  })

  it('fills how the task starts in the form’s words, and only those', () => {
    expect(previewOf({ start_mode: '直接开始' }, terms).fields).toEqual({ startMode: 'DIRECT' })
    expect(previewOf({ start_mode: '申请制' }, terms).fields).toEqual({ startMode: 'APPLY' })
    expect(previewOf({ start_mode: '直接' }, terms).fields).toEqual({})
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

describe('previewOf：参与、时间和更多设置', () => {
  const named = {
    categories: [],
    topics: [],
    mentors: [{ handle: 'wangli', name: '王立' }],
    domainGroups: [{ id: 3, name: '本校邮箱' }],
  }

  it('导师按名字或账号，访问范围按域名组名字，AI 指导写进本题单独的指导', () => {
    const shown = previewOf(
      {
        draft: 41,
        mentors: ['王立', '@wangli', '不认识的人'],
        team_locking: '开始做这道题后锁定',
        registration_start: '2026-11-01 08:00',
        access: ['本校邮箱'],
        ai_guidance: '先写需求说明',
      },
      named
    )
    expect(shown.fields).toMatchObject({
      mentors: ['wangli', 'wangli'],
      teamLockingPolicy: 'LOCK_ON_APPROVAL',
      accessControlEnabled: true,
      accessDomainGroupIds: [3],
      teaching: { systemPrompt: '先写需求说明' },
      teachingOwn: true,
    })
    expect(new Date(shown.fields.registrationStartAt as number).getHours()).toBe(8)
    expect(shown.writing).toBe('ai_guidance')
  })

  it('空列表是全部成员能领，空指导回到空间默认', () => {
    const shown = previewOf({ access: [], ai_guidance: '' }, named)
    expect(shown.fields).toMatchObject({ accessControlEnabled: false, teaching: {}, teachingOwn: false })
  })
})

describe('previewOf：AI 的设置', () => {
  const named = {
    categories: [],
    topics: [],
    materials: [{ id: 7, name: '第 6 周讲义.pdf' }],
    teammates: [{ name: 'academic-research', title: '学术研究' }],
    teaching: { mode: 'guide' as const, systemPrompt: '先问他试过什么' },
  }

  it('芝士写到的几格换上，没写的照草稿现在的样子；队友和资料按名字对上', () => {
    const shown = previewOf(
      {
        draft: 41,
        ai_teammate: '学术研究',
        ai_private_guidance: '参考答案在第 12 页',
        ai_goals: ['能自己写出 LRU'],
        ai_materials: ['第 6 周讲义.pdf', '不在资料库里的'],
        ai_skills: ['wolfram'],
      },
      named
    )
    expect(shown.fields).toMatchObject({
      defaultRole: 'academic-research',
      teaching: {
        mode: 'guide',
        systemPrompt: '先问他试过什么',
        privatePrompt: '参考答案在第 12 页',
        learningGoals: ['能自己写出 LRU'],
        materialIds: [7],
        skills: ['wolfram'],
      },
      teachingOwn: true,
    })
  })

  it('不认识的队友先不填，等服务端那一份', () => {
    expect(previewOf({ ai_teammate: '没有这位' }, named).fields.defaultRole).toBeUndefined()
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
