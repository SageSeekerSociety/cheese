// 草稿落盘的边界：什么算空、多久算过期、存不下时怎么办、以及换人登录要抹干净。
//
// 这一层是 localStorage 的读写，出错的方式全是「读回来一份不是当时写进去的东西」，
// 所以每条用例都盯着**读回来的那一份**，而不是「写没写成功」。
import type { Block, ChatAttachment } from '@/cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { clearComposerDrafts, forgetComposerDraft, loadComposerDraft, saveComposerDraft } from './composerDrafts'

const HOUR = 60 * 60 * 1000
const DAY = 24 * HOUR

const att = (path: string): ChatAttachment => ({ path, mime: 'image/png' })

const blockOf = (id: string) => ({ id, content: '被回复的那条' }) as unknown as Block

/** localStorage 里现有的键（要确认我们没在别人的地盘上乱写）。 */
function storedKeys(): string[] {
  const keys: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key !== null) keys.push(key)
  }
  return keys.sort()
}

beforeEach(() => {
  localStorage.clear()
})

describe('写进去读得回来', () => {
  it('正文、回复目标、附件都原样回来', () => {
    saveComposerDraft('t1', { draft: '打了一半的话', reply: blockOf('m1'), atts: [att('a.png')] })
    const saved = loadComposerDraft('t1')

    expect(saved?.draft).toBe('打了一半的话')
    expect(saved?.reply?.id).toBe('m1')
    expect(saved?.atts).toEqual([att('a.png')])
  })

  it('没有草稿的话题读回来是 null', () => {
    expect(loadComposerDraft('never-saved')).toBeNull()
  })

  it('清空输入框（存一份空的）等于没有草稿——空记录不会把上次的顶掉', () => {
    saveComposerDraft('t1', { draft: '先打一句', reply: null, atts: [] })
    saveComposerDraft('t1', { draft: '   ', reply: null, atts: [] })

    expect(loadComposerDraft('t1')).toBeNull()
    expect(storedKeys()).toEqual([])
  })

  it('只发出去的消息也算「有东西」：发出后草稿该消失', () => {
    saveComposerDraft('t1', { draft: '发出去了', reply: null, atts: [att('a.png')] })
    // 发送之后正文清空、附件也被清掉，剩下一次空写入——记录应当被删掉，而不是
    // 留下一份带附件、下次打开时又冒出来的草稿。
    saveComposerDraft('t1', { draft: '', reply: null, atts: [] })
    expect(loadComposerDraft('t1')).toBeNull()
  })
})

describe('过期与淘汰', () => {
  it('放了一周以上就不该再弹回来', () => {
    const t0 = 1_700_000_000_000
    saveComposerDraft('t1', { draft: '上周打的字', reply: null, atts: [] }, t0)

    expect(loadComposerDraft('t1', t0 + 6 * DAY)?.draft).toBe('上周打的字')
    expect(loadComposerDraft('t1', t0 + 8 * DAY)).toBeNull()
    // 读的时候顺手删掉，不留一条永远读不出来的记录占地方。
    expect(storedKeys()).toEqual([])
  })

  it('话题太多时丢掉最旧的那几条，最多留 20 个', () => {
    const t0 = 1_700_000_000_000
    for (let i = 0; i < 25; i++) {
      saveComposerDraft(`t${i}`, { draft: `第 ${i} 条`, reply: null, atts: [] }, t0 + i * 1000)
    }

    const kept = storedKeys()
    expect(kept.length).toBe(20)
    expect(loadComposerDraft('t0', t0 + 60_000)).toBeNull()
    expect(loadComposerDraft('t24', t0 + 60_000)?.draft).toBe('第 24 条')
  })
})

describe('坏数据不该让输入框出问题', () => {
  it('读不懂的内容当作没有草稿，并且把那条删掉', () => {
    localStorage.setItem('cheese.composer.v1:t1', '{这不是 JSON')
    expect(loadComposerDraft('t1')).toBeNull()
    expect(storedKeys()).toEqual([])
  })

  it('字段类型不对（正文不是字符串）也当作没有', () => {
    localStorage.setItem('cheese.composer.v1:t1', JSON.stringify({ savedAt: Date.now(), draft: 42 }))
    expect(loadComposerDraft('t1')).toBeNull()
  })

  it('附件数组里混进了别的形状，只丢那一条', () => {
    localStorage.setItem(
      'cheese.composer.v1:t1',
      JSON.stringify({
        savedAt: Date.now(),
        draft: 'x',
        reply: null,
        atts: [att('good.png'), { path: 'no-mime' }, null, 'nope'],
      })
    )
    expect(loadComposerDraft('t1')?.atts).toEqual([att('good.png')])
  })

  it('草稿太大就干脆不存（localStorage 只有几 MB，写满了会抛）', () => {
    saveComposerDraft('t1', { draft: 'x'.repeat(300_000), reply: null, atts: [] })
    expect(loadComposerDraft('t1')).toBeNull()
  })
})

describe('清理', () => {
  it('忘掉一个话题只删它自己', () => {
    saveComposerDraft('t1', { draft: 'a', reply: null, atts: [] })
    saveComposerDraft('t2', { draft: 'b', reply: null, atts: [] })

    forgetComposerDraft('t1')
    expect(loadComposerDraft('t1')).toBeNull()
    expect(loadComposerDraft('t2')?.draft).toBe('b')
  })

  it('换人登录时全部抹掉，且不碰 localStorage 里别人的键', () => {
    localStorage.setItem('accessToken', 'someone-elses-token')
    localStorage.setItem('user', '{"id":1}')
    saveComposerDraft('t1', { draft: '上一个人的半句话', reply: null, atts: [] })
    saveComposerDraft('t2', { draft: '另一句', reply: null, atts: [] })

    clearComposerDrafts()

    expect(loadComposerDraft('t1')).toBeNull()
    expect(loadComposerDraft('t2')).toBeNull()
    expect(storedKeys()).toEqual(['accessToken', 'user'])
  })
})
