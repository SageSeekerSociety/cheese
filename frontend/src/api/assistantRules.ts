// 「芝士」设置页的接口：这个人告诉自己的芝士哪些事一定先问、哪些可以直接做，以及芝士
// 最近替他做了什么（`/assistant/rules`、`/assistant/activity`）。只服务这个人自己，芝士
// 拿着它的凭据进不来，所以改不了自己的规矩。

import { request } from './http'

export interface AssistantRuleCategory {
  code: string
  label: string
}

export interface AssistantRules {
  ask: string[]
  allowText: string
  askText: string
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

export function getAssistantRules(): Promise<AssistantRules> {
  return request<AssistantRules>('/assistant/rules')
}

export function saveAssistantRules(
  rules: Pick<AssistantRules, 'ask' | 'allowText' | 'askText'>
): Promise<AssistantRules> {
  return request<AssistantRules>('/assistant/rules', { method: 'PUT', body: JSON.stringify(rules) })
}

export function getAssistantActivity(): Promise<{ items: AssistantActivityItem[] }> {
  return request<{ items: AssistantActivityItem[] }>('/assistant/activity')
}
