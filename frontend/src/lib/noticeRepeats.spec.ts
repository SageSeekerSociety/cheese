// 同一个人连着做的几件事不刷屏：合成一行，说清做了什么，点开每一条都还在。
import type { Block } from '../cx_types'

import { beforeEach, describe, expect, it } from 'vitest'

import { repeatsLine } from './noticeRepeats'
import { collapseNotices } from './platformNotice'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

let seq = 0
function at(): string {
  seq += 1
  return `2026-10-06T08:${String(Math.floor(seq / 60)).padStart(2, '0')}:${String(seq % 60).padStart(2, '0')}Z`
}

function archived(actor: string, room: string): Block {
  return {
    id: `a${seq}`,
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: actor,
    content: `<@${actor}> 归档了频道「${room}」`,
    created_at: at(),
    meta: { platform: true, i18n: { content: { key: 'roomArchived', params: { actor: `<@${actor}>`, room } } } },
  } as unknown as Block
}

function retitled(actor: string): Block {
  return {
    id: `r${seq}`,
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: actor,
    content: `<@${actor}> 改了频道名`,
    created_at: at(),
    meta: { platform: true, i18n: { content: { key: 'roomRenamed', params: { actor: `<@${actor}>` } } } },
  } as unknown as Block
}

function said(author: string, content: string): Block {
  return {
    id: `m${seq}`,
    conversation_id: 't',
    kind: 'message',
    author_type: 'participant',
    author,
    content,
    created_at: at(),
  } as unknown as Block
}

const merged = (blocks: Block[]) =>
  collapseNotices(blocks)
    .map((row) => row.notice)
    .filter((n) => n?.mode === 'repeats')

describe('one person doing several things in a row', () => {
  it('becomes one line that keeps every one of them', () => {
    const blocks = [archived('wang', 'A'), archived('wang', 'B'), retitled('wang'), archived('wang', 'C')]
    const [group] = merged(blocks)
    expect(group?.mode === 'repeats' && group.rows.map((r) => r.block.id)).toEqual(blocks.map((b) => b.id))
  })

  it('says what was done, by kind, and how many of each', () => {
    const [group] = merged([archived('wang', 'A'), archived('wang', 'B'), retitled('wang')])
    if (group?.mode !== 'repeats') throw new Error('not merged')
    const line = repeatsLine(group.rows, '王昌鑫')
    expect(line).toContain('王昌鑫')
    expect(line).toContain('2')
    expect(line).toContain('1')
  })

  it('does not merge different people', () => {
    expect(merged([archived('wang', 'A'), archived('peng', 'B')])).toEqual([])
  })

  it('breaks at a message in between', () => {
    expect(merged([archived('wang', 'A'), said('peng', '这几个还要吗'), archived('wang', 'B')])).toEqual([])
  })

  it('leaves a single action as it was', () => {
    expect(merged([archived('wang', 'A')])).toEqual([])
  })
})
