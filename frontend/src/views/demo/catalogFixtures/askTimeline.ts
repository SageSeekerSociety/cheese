/** Questions in their real timeline, with ordinary messages on both sides. */
import type { Block } from '@/cx_types'

import { chatTimelineProps } from './chat'
import { ROOM_REFS } from './common'

import { groupAskBlocks } from '@/lib/askGroups'

export function askTimelineProps(viewer = 'reader', long = false): Record<string, unknown> {
  // i18n-data: authored example messages and personal display names for the catalog.
  const names = {
    cheese: '芝士Opus',
    reader: '旁观者',
    alice: long ? '负责报告时间口径确认的项目协作者AlexandertheReviewer' : '蔡松洋',
  }
  const block = (id: string, content: string, meta: Block['meta'] = null): Block =>
    ({
      id,
      conversation_id: 'demo-room',
      kind: 'message',
      author: 'cheese',
      author_type: 'participant',
      content,
      created_at: '2026-11-18T05:00:00Z',
      meta,
    }) as Block
  const meta: Block['meta'] = {
    notice_id: 'demo-time-scope',
    asked: ['alice'],
    options: [{ text: '最近一周' }, { text: '最近一个月' }],
    answer_log: long
      ? [
          {
            by: 'alice',
            kind: 'note',
            option: null,
            note: '请保留完整回答与长名字，报告范围使用最近一个月，并把跨月的数据分别说明。'.repeat(3),
            at: '2026-11-18T05:01:00Z',
          },
        ]
      : [],
  }
  const blocks = [
    block('ask-before', '报告需要先确定统计范围。'),
    block('ask-first', 'PDF 里的「最近」按多长时间算？', meta),
    block('ask-second', '报告采用哪个统计范围？', meta),
    block('ask-after', '确认范围后，再整理报告中的数据。'),
  ]
  const grouped = groupAskBlocks(blocks)
  return chatTimelineProps({
    rows: blocks.filter((b) => !grouped.hidden.has(b.id)).map((b) => ({ block: b, notice: null, run: [b] })),
    askGroups: grouped.members,
    refs: { ...ROOM_REFS, mentionNames: { ...ROOM_REFS.mentionNames, ...names } },
    viewer,
    displayName: (b: Block) => names[b.author as keyof typeof names] ?? b.author,
    dayLabels: new Map([['ask-first', '今天']]),
  })
}
