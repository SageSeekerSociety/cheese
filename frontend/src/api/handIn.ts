import type { AcceptCard } from '../cx_types'

import { changesAwaiting } from './awaitingChanged'
import { request } from './http'

// ---- 成员自己提交审阅 ----
// 一件任务里保存过的修改，只能作为交付的一版进项目：人也走提交审阅这条路，和 AI 队友
// 递的是同一张卡、同一套审阅和采纳（后端 `machine.hand_in`）。

/** 任务有没有不在最近那一版里的修改（「有未提交的修改」），以及有没有一版在等审阅。 */
export interface HandInStanding {
  unhanded: boolean
  waiting: boolean
}

export interface HandInFile {
  path: string
  additions: number
  deletions: number
}

/** 提交面板要显示的：这次会交进去的文件、默认交给谁审阅；有一版在等时它的说明。 */
export interface HandInPanel {
  files: HandInFile[]
  again: boolean
  subject: string | null
  body: string | null
  reviewers: string[]
}

export interface HandInDraft {
  subject: string
  body: string
  /** 模型写的；false 是按改动的文件拼的那一行。 */
  drafted: boolean
}

export interface HandedIn {
  card: AcceptCard
  /** 「提交并采纳」：这一版的采纳已经定下，代码托管服务算出能合并、项目的规则满足后由平台合并。 */
  adopting: boolean
}

const at = (taskId: string) => `/topics/${encodeURIComponent(taskId)}/hand-in`

export function getHandInStanding(taskId: string): Promise<HandInStanding> {
  return request<HandInStanding>(at(taskId))
}

export function getHandInPanel(taskId: string): Promise<HandInPanel> {
  return request<HandInPanel>(`${at(taskId)}/panel`)
}

export function draftHandIn(taskId: string): Promise<HandInDraft> {
  return request<HandInDraft>(`${at(taskId)}/draft`, { method: 'POST' })
}

export function handIn(
  taskId: string,
  body: { subject: string; body: string; reviewer_handle: string | null; accept: boolean }
): Promise<HandedIn> {
  return changesAwaiting(request<HandedIn>(at(taskId), { method: 'POST', body: JSON.stringify(body) }))
}
