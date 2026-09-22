/**
 * 表单落盘那一层的边界。
 *
 * 这一层和 `composerDrafts.ts` 是同一种东西，所以它要钉住的也是同一组边界：**盘上
 * 的内容是不可信的输入**（它经过 localStorage，本机上的扩展、别的标签页、以及用户
 * 自己都能改），而它最终会走进 `POST /feedback` 的请求体里。多带一个没点名的字段，
 * 就是让本机上的一个字符串直接变成请求里的一个字段。
 *
 * 另外三条是「不许把用户的字弄丢 / 不许把没有的东西说成有」：空草稿不该写盘、过期
 * 的记录该被清掉、读不懂的记录该被清掉而不是每次白读一遍。
 */
import type { FeedbackDraft } from '@/stores/feedback'

import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearLiveFeedbackDraft,
  forgetFeedbackDraft,
  isDraftMeaningful,
  loadFeedbackDraft,
  parkFeedbackDraft,
  saveFeedbackDraft,
  takeParkedFeedbackDraft,
} from '@/lib/feedbackDraft'

const KEY = 'cheese.feedback-draft.v1:current'

function draft(over: Partial<FeedbackDraft> = {}): FeedbackDraft {
  return {
    kind: 'bug',
    title: '标题',
    body: '正文',
    repro: '',
    expectation: '',
    tags: [],
    attachContext: false,
    visibility: 'public',
    ...over,
  }
}

/** 直接往盘上写一份（模拟「别人改过」），返回读得出来的那个原始值。 */
function writeRaw(value: unknown): void {
  localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), draft: value, parked: null }))
}

beforeEach(() => {
  localStorage.clear()
})

describe('有没有东西', () => {
  it('标题、正文、标签、提案卡指针，任何一样有内容都算', () => {
    expect(isDraftMeaningful(draft())).toBe(true)
    expect(isDraftMeaningful(draft({ title: '  ', body: '' }))).toBe(false)
    expect(isDraftMeaningful(draft({ title: '', body: '  ' }))).toBe(false)
    expect(isDraftMeaningful(draft({ title: '', body: '', tags: ['移动端'] }))).toBe(true)
    expect(isDraftMeaningful(draft({ title: '', body: '', expectation: '应该这样' }))).toBe(true)
    expect(isDraftMeaningful(draft({ title: '', body: '', proposal: { topicId: 't', blockId: 'b' } }))).toBe(true)
    expect(isDraftMeaningful(null)).toBe(false)
  })

  it('空草稿不写盘，也不把上次那份顶掉', () => {
    saveFeedbackDraft(draft({ title: '上次那份', body: '正文' }))
    saveFeedbackDraft(draft({ title: '', body: '' }))
    expect(loadFeedbackDraft()?.title).toBe('上次那份')
  })
})

describe('盘上的内容不可信', () => {
  it('多出来的字段不会跟着走', () => {
    writeRaw({ ...draft(), evil: 'x', visibility: 'public' })
    const loaded = loadFeedbackDraft()
    expect(loaded).not.toBeNull()
    expect(Object.keys(loaded ?? {})).not.toContain('evil')
    expect(loaded?.title).toBe('标题')
  })

  it('类型和可见范围不在词表里就当没有', () => {
    writeRaw({ ...draft(), kind: 'superadmin' })
    expect(loadFeedbackDraft()).toBeNull()
    writeRaw({ ...draft(), visibility: 'everyone' })
    expect(loadFeedbackDraft()).toBeNull()
  })

  it('连标题都没有的记录读不出来', () => {
    writeRaw({ body: '正文' })
    expect(loadFeedbackDraft()).toBeNull()
  })

  it('形状不对的提案卡指针当没有 —— 留着它「发送」会打不开', () => {
    writeRaw({ ...draft(), proposal: { topicId: 't' } })
    expect(loadFeedbackDraft()?.proposal).toBeUndefined()
  })

  it('一截现场不当成现场', () => {
    writeRaw({ ...draft(), fromAgent: { whatHappened: 42, repro: 'x' } })
    expect(loadFeedbackDraft()?.fromAgent).toEqual({
      whatHappened: '',
      repro: 'x',
      evidence: '',
    })
  })

  it('读不懂的、过期的都清掉，不留着每次白读', () => {
    localStorage.setItem(KEY, '{ 这不是 json')
    expect(loadFeedbackDraft()).toBeNull()
    expect(localStorage.getItem(KEY)).toBeNull()

    localStorage.setItem(
      KEY,
      JSON.stringify({ savedAt: Date.now() - 8 * 24 * 60 * 60 * 1000, draft: draft(), parked: null })
    )
    expect(loadFeedbackDraft()).toBeNull()
    expect(localStorage.getItem(KEY)).toBeNull()
  })
})

describe('两个槽位', () => {
  it('park 之后「正在写的」那一格是空的', () => {
    saveFeedbackDraft(draft({ title: '我的' }))
    parkFeedbackDraft(draft({ title: '我的' }))
    expect(loadFeedbackDraft()).toBeNull()
    expect(takeParkedFeedbackDraft()?.title).toBe('我的')
    // 取出来之后那一格也空了（它回到表单上了，不该还留一份）。
    expect(takeParkedFeedbackDraft()).toBeNull()
  })

  it('清「正在写的」那一格时 parked 留着', () => {
    parkFeedbackDraft(draft({ title: '被替换下去的' }))
    saveFeedbackDraft(draft({ title: '正在写的' }))
    clearLiveFeedbackDraft()
    expect(loadFeedbackDraft()).toBeNull()
    expect(takeParkedFeedbackDraft()?.title).toBe('被替换下去的')
  })

  it('两格都空了就整条删掉，不留一条空记录', () => {
    saveFeedbackDraft(draft())
    clearLiveFeedbackDraft()
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('forget 把两格都抹掉（换人登录那条路）', () => {
    parkFeedbackDraft(draft({ title: '旧的' }))
    saveFeedbackDraft(draft({ title: '新的' }))
    forgetFeedbackDraft()
    expect(localStorage.getItem(KEY)).toBeNull()
    expect(loadFeedbackDraft()).toBeNull()
    expect(takeParkedFeedbackDraft()).toBeNull()
  })
})

describe('写不进去也不让表单出问题', () => {
  it('超过上限就不存，但也不抛', () => {
    saveFeedbackDraft(draft({ body: 'x'.repeat(200_001) }))
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('setItem 抛异常时静默跳过', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => saveFeedbackDraft(draft())).not.toThrow()
    spy.mockRestore()
  })
})
