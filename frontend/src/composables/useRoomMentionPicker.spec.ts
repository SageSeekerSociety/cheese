// 对话输入框的 @ 和 #：@ 叫人和 AI 队友，# 引用频道和文档（#3827）。一个符号一类东西。
import type { Topic } from '../cx_types'

import { effectScope, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { useRoomMentionPicker } from './useRoomMentionPicker'

const channel = { id: 'c1', title: '标注讨论', kind: 'room', status: 'active', members_only: false } as unknown as Topic
const secret = { id: 'c2', title: '合作洽谈', kind: 'room', status: 'active', members_only: true } as unknown as Topic

function picker(text: string) {
  const draft = ref(text)
  const scope = effectScope()
  const p = scope.run(() =>
    useRoomMentionPicker({
      draft,
      writeTokens: true,
      topic: () => channel,
      mentionPool: () => [{ handle: 'liming', label: '李明', agent: false, avatar: null }],
      topicList: () => [channel, secret],
      documentList: () => [
        { id: 'd1', title: '数据集说明' },
        { id: 'd2', title: '标注规范 v2' },
      ],
      focus: () => {},
      scrollActiveIntoView: () => {},
    })
  )!
  return { draft, p }
}

describe('打 #', () => {
  it('列出频道和文档，私密频道不在里面', () => {
    const { p } = picker('看 #')
    expect(p.matches.value.map((m) => [m.kind, m.label])).toEqual([
      ['topic', '标注讨论'],
      ['document', '数据集说明'],
      ['document', '标注规范 v2'],
    ])
  })

  it('挑中一份文档，写进正文的是它的引用', async () => {
    const { draft, p } = picker('按 #标注')
    const doc = p.matches.value.find((m) => m.kind === 'document')!
    p.pick(doc)
    await nextTick()
    expect(draft.value).toBe('按 <#d2> ')
  })

  it('紧跟在字母后面的 # 不开菜单', () => {
    expect(picker('C#').p.query.value).toBeNull()
  })
})

describe('打 @', () => {
  it('只列人，不列频道和文档', () => {
    const { p } = picker('@')
    expect(p.matches.value.every((m) => m.kind === 'member' || m.kind === 'broadcast')).toBe(true)
  })
})
