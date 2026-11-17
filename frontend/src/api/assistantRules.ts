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
  /**
   * `filling`：建的时候留了空，芝士正在补（补完变成 active）。
   * `done`：只运行一次的，已经运行过；3 天后自动删除。
   */
  state: 'active' | 'paused' | 'filling' | 'done'
  /**
   * 暂停的原因：`person` 自己暂停，`credits` 额度用完，`away` 长时间未登录，
   * `unfilled` 芝士没补上。
   */
  paused: string
  nextRunAt: string | null
  /** 只做一次：在建它的那段对话里做，做完就结束（「正在等的事」）。 */
  once: boolean
  /** 建它的那段对话；在设置页填表建的没有。 */
  conversationId: string | null
  /** `filling` 时还差哪几项，写给人看的词（「名字」「什么时候跑」）。 */
  missing: string[]
  /** 填表时选的那一样：`schedule` 或七种事件之一；空着是让芝士定。 */
  trigger: string
  spec: Record<string, unknown>
  timezone: string
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

export function setAssistantRoutine(
  id: string,
  action: 'pause' | 'resume' | 'delete' | 'run-now'
): Promise<AssistantRoutines> {
  return action === 'delete'
    ? request<AssistantRoutines>(`/assistant/routines/${id}`, { method: 'DELETE' })
    : request<AssistantRoutines>(`/assistant/routines/${id}/${action}`, { method: 'POST' })
}

/** 设置页那张表填的东西。除了「目标」，每一项都可以留空，芝士先补上再跑。
 *
 *  时间和芝士那张表的空是同一套（`routes/assistant_agent.py` 的 `RoutineIn`）：
 *  每种频率只问它自己那一刻 —— 每时问第几分钟，每周问星期几，每月问几号，
 *  「只在某一刻」问具体时刻。等任务审阅的那些要说是哪个任务（地址栏里的引用），
 *  或者 `mine` 表示「我提的任务」；`decided` 还能只说采纳还是退回。 */
export interface AssistantRoutineForm {
  title: string
  /** 「目标」：这一条要做的事，必填。 */
  note: string
  /** 「补充说明」：目标没说到的，可以空着。 */
  explain: string
  trigger: string
  freq: string
  time: string
  minute: number | null
  weekdays: number[] | null
  day: number | null
  at: string
  timezone: string
  /** `filed` / `decided`：我提的任何任务。 */
  mine: boolean
  /** `filed` / `decided`：这一个任务，地址栏里的引用（`ml-course/tasks/12`）。 */
  task: string
  /** `decided`：只说采纳（`accepted`）或退回（`rejected`）；空着是两种都算。 */
  outcome: string
  /** 事件规则只等第一次。 */
  once: boolean
}

export function createAssistantRoutine(body: AssistantRoutineForm): Promise<AssistantRoutines> {
  return request<AssistantRoutines>('/assistant/routines', { method: 'POST', body: JSON.stringify(body) })
}

export function updateAssistantRoutine(id: string, body: AssistantRoutineForm): Promise<AssistantRoutines> {
  return request<AssistantRoutines>(`/assistant/routines/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
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
