// 「芝士」设置页的接口：这个人告诉自己的芝士哪些事需要确认、哪些无需确认，芝士最近替他
// 做了什么，以及芝士自己定时或在某件事发生时做的例行事务（`/assistant/rules`、
// `/assistant/activity`、`/assistant/routines`）。只服务这个人自己，芝士拿着它的凭据进不来，
// 所以改不了自己的规矩。

import { request } from './http'

export interface AssistantRuleCategory {
  code: string
  label: string
}

export interface AssistantRules {
  /** 勾上的几类：每次都请他确认。 */
  ask: string[]
  /** 他自己添加的条目，一条一句。 */
  askItems: string[]
  allowItems: string[]
  categories: AssistantRuleCategory[]
}

export interface AssistantActivityItem {
  id: number
  kind: string
  summary: string
  verdict: 'allowed' | 'asked'
  stage: string
  reason: string
  at: string
}

export interface AssistantRoutineRun {
  status: 'queued' | 'running' | 'done' | 'failed' | 'skipped'
  at: string
  asked: number
  conversationId: string | null
}

export interface AssistantRoutine {
  id: string
  title: string
  note: string
  /** 什么时候运行，后端写好的一句。 */
  when: string
  /** `done`：只运行一次的，已经运行过；3 天后自动删除。 */
  state: 'active' | 'paused' | 'done'
  /** 暂停的原因：`person` 自己暂停，`credits` 额度用完，`away` 长时间未登录。 */
  paused: string
  nextRunAt: string | null
  /** 只做一次：在建它的那段对话里做，做完就结束（「正在等的事」）。 */
  once: boolean
  /** 建它的那段对话。 */
  conversationId: string | null
  lastRun: AssistantRoutineRun | null
  /** 近 7 天的运行花了多少点。 */
  weekCredits: number
}

export interface AssistantRoutines {
  routines: AssistantRoutine[]
  limit: number
}

export function getAssistantRules(): Promise<AssistantRules> {
  return request<AssistantRules>('/assistant/rules')
}

export function saveAssistantRules(
  rules: Pick<AssistantRules, 'ask' | 'askItems' | 'allowItems'>
): Promise<AssistantRules> {
  return request<AssistantRules>('/assistant/rules', { method: 'PUT', body: JSON.stringify(rules) })
}

export function getAssistantActivity(): Promise<{ items: AssistantActivityItem[] }> {
  return request<{ items: AssistantActivityItem[] }>('/assistant/activity')
}

export function getAssistantRoutines(): Promise<AssistantRoutines> {
  return request<AssistantRoutines>('/assistant/routines')
}

export function setAssistantRoutine(id: string, action: 'pause' | 'resume' | 'delete'): Promise<AssistantRoutines> {
  return action === 'delete'
    ? request<AssistantRoutines>(`/assistant/routines/${id}`, { method: 'DELETE' })
    : request<AssistantRoutines>(`/assistant/routines/${id}/${action}`, { method: 'POST' })
}

/** 芝士记得的一件事：一句描述（列在每段新对话开头）和正文。 */
export interface AssistantMemoryItem {
  name: string
  description: string
  /** `user` 你是谁，`feedback` 做事的要求，`project` 进行中的安排，`reference` 外部资料。 */
  type: string
  text: string
  updatedAt: string
}

export interface AssistantMemory {
  /** 关掉时记下的保留，芝士不用也不记。 */
  enabled: boolean
  items: AssistantMemoryItem[]
}

export function getAssistantMemory(): Promise<AssistantMemory> {
  return request<AssistantMemory>('/assistant/memory')
}

export function setAssistantMemoryEnabled(enabled: boolean): Promise<AssistantMemory> {
  return request<AssistantMemory>('/assistant/memory', { method: 'PUT', body: JSON.stringify({ enabled }) })
}

export function saveAssistantMemoryItem(name: string, description: string, text: string): Promise<AssistantMemory> {
  return request<AssistantMemory>(`/assistant/memory/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify({ description, text }),
  })
}

export function deleteAssistantMemoryItem(name: string): Promise<AssistantMemory> {
  return request<AssistantMemory>(`/assistant/memory/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function clearAssistantMemory(): Promise<AssistantMemory> {
  return request<AssistantMemory>('/assistant/memory', { method: 'DELETE' })
}
