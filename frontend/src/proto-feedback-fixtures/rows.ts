/**
 * proto-feedback-fixtures/rows.ts — 那一份假数据本身：手写的加铺量的，外加读一条和
 * 新建一条。
 *
 * 这里是**活的**：删一条反馈（路由里的 `ROWS.splice`）、新建一条（`create`）都改这个
 * 数组，列表、计数、看板读的是同一份。数组本身从不重新赋值，所以别的分片 import 到的
 * 一直是同一个对象。
 */
import type { FeedbackCreateBody, FeedbackDetail } from '@/cx_types'

import { FILLER } from './filler'
import { HANDWRITTEN } from './handwritten'
import { ME, row } from './support'

export const ROWS: FeedbackDetail[] = [
  ...HANDWRITTEN,
  ...FILLER.map((f, i) =>
    row({
      id: `fb-${1029 - i}`,
      display: `FB-${1029 - i}`,
      kind: f.kind,
      title: f.title,
      summary: f.summary,
      status: f.status,
      // 不传优先级就是 `normal`：`row()` 里那一个默认值是一处，不在每条上重写一遍。
      priority: f.p,
      author: f.author,
      authorIsAgent: f.agent,
      visibility: f.vis ?? 'public',
      security: f.sec ?? false,
      assignee: f.who,
      supports: f.supports,
      minutesAgo: f.h * 60,
      problem: f.summary,
    })
  ),
]

let nextId = 1043

export function find(id: string): FeedbackDetail | undefined {
  return ROWS.find((item) => item.id === id)
}

export function create(body: FeedbackCreateBody): FeedbackDetail {
  const id = `fb-${nextId}`
  const created = row({
    id,
    display: `FB-${nextId}`,
    kind: body.kind,
    title: body.title || '（无标题）',
    summary: body.summary || body.problem || '',
    status: 'received',
    visibility: body.visibility ?? 'public',
    author: ME,
    submittedBy: ME,
    supports: 0,
    minutesAgo: 0,
    problem: body.problem ?? '',
    whatHappened: body.what_happened ?? undefined,
    repro: body.repro ?? undefined,
    evidence: body.evidence ?? undefined,
    environment: body.environment ?? undefined,
  })
  nextId += 1
  ROWS.unshift(created)
  return created
}
