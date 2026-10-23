// 对话输入框的 @ 和 #：@ 叫人和 AI 队友，# 引用频道和文档（#3827）。一个符号一类东西。
import type { Topic } from '../cx_types'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { effectScope, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'

import { useRoomMentionPicker } from './useRoomMentionPicker'

import { setLocale } from '@/i18n'

const DEFAULT_POOL: MentionPoolEntry[] = [{ handle: 'liming', label: '李明', agent: false, avatar: null }]

beforeEach(() => {
  setLocale('zh-CN')
})

const channel = { id: 'c1', title: '标注讨论', kind: 'room', status: 'active', members_only: false } as unknown as Topic
const secret = { id: 'c2', title: '合作洽谈', kind: 'room', status: 'active', members_only: true } as unknown as Topic

function picker(text: string, pool: MentionPoolEntry[] = DEFAULT_POOL) {
  const draft = ref(text)
  const scope = effectScope()
  const p = scope.run(() =>
    useRoomMentionPicker({
      draft,
      writeTokens: true,
      topic: () => channel,
      mentionPool: () => pool,
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

describe('@ 候选：不截断，频道外的人单独一组', () => {
  const person = (handle: string, label: string, outside = false): MentionPoolEntry => ({
    handle,
    label,
    agent: false,
    avatar: null,
    outsideTopic: outside,
  })

  it('名单比 7 长也全部列出来——第 8 个人以后照样挑得到', () => {
    const many = Array.from({ length: 12 }, (_, i) => person(`u${i}`, `同事${i}`, true))
    const { p } = picker('@', many)
    const people = p.matches.value.filter((m) => m.kind === 'member')
    expect(people).toHaveLength(12)
    expect(people.at(-1)?.label).toBe('同事11')
  })

  it('频道里的人在前，不在频道的人在后，并挂着分组标题', () => {
    // 池子里顺序故意把外人放在前面：排序不许信池子给的顺序。
    const { p } = picker('@', [
      person('carol', 'Carol', true),
      person('alice', 'Alice'),
      { handle: 'cheese-seat', label: '芝士', avatar: null, agent: true },
    ])
    expect(p.matches.value.map((m) => m.label)).toEqual(['芝士', '所有人', '在线成员', 'Alice', 'Carol'])
    expect(p.matches.value.find((m) => m.label === 'Carol')?.group).toBe('不在频道')
    expect(p.matches.value.find((m) => m.label === 'Alice')?.group).toBeUndefined()
  })

  it('不在频道的 AI 队友不占第一格：跟着频道外的那一组走', () => {
    const { p } = picker('@', [
      { handle: 'cheese-other', label: '芝士ni', avatar: null, agent: true, outsideTopic: true },
      { handle: 'cheese-seat', label: '芝士', avatar: null, agent: true },
      person('alice', 'Alice'),
    ])
    expect(p.matches.value[0].label).toBe('芝士')
    expect(p.matches.value.map((m) => m.label)).toEqual(['芝士', '所有人', '在线成员', 'Alice', '芝士ni'])
    expect(p.matches.value.find((m) => m.label === '芝士ni')?.group).toBe('不在频道')
  })
})
