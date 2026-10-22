// 文档里打 #：频道一组、文档一组，正在写的这一份不引用自己（#3827）。
import type { MentionPoolEntry } from '../composables/useRoomMentionPicker'

import { describe, expect, it } from 'vitest'

import { documentItems, hashItems, peopleItems, topicItems } from './docMentionMenu'

const docs = [
  { id: 'd1', title: '数据集说明' },
  { id: 'd2', title: '标注规范 v2' },
]

describe('文档里的 @ 候选', () => {
  it('人不截断：名单比 7 长也全列出来，第 8 个人以后照样挑得到', () => {
    const pool: MentionPoolEntry[] = Array.from({ length: 9 }, (_, i) => ({
      handle: `u${i}`,
      label: `同事${i}`,
      agent: false,
      avatar: null,
    }))
    expect(peopleItems(pool, '').map((i) => i.label)).toHaveLength(9)
  })
})

describe('文档里的 # 候选', () => {
  it('不列正在写的这一份', () => {
    expect(documentItems(docs, '', 'd2').map((i) => i.token)).toEqual(['<#d1>'])
  })

  it('按标题筛', () => {
    expect(documentItems(docs, '规范').map((i) => i.label)).toEqual(['标注规范 v2'])
  })

  it('频道和文档都有时分两组，频道在前', () => {
    const topics = topicItems([{ id: 'c1', title: '标注讨论', kind: 'room', status: 'active' }] as never, '')
    const items = hashItems(topics, documentItems(docs, ''))
    expect(items.map((i) => i.kind)).toEqual(['topic', 'document', 'document'])
    expect(new Set(items.map((i) => i.group)).size).toBe(2)
  })
})
