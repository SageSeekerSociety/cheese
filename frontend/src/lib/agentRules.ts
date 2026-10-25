// 芝士建的自动化在回答里那张卡片要的东西：它此刻的样子，和外壳提供给卡片的读写口。
import type { InjectionKey } from 'vue'
import type { AssistantRoutine } from '@/api'

/** 一条自动化此刻的样子。`ended`：列表里已经没有它了（做完后清掉的，或在设置里删了）。 */
export type AgentRuleNow =
  | { state: 'waiting' | 'on' | 'started' | 'gaveUp' | 'cancelled' | 'ended' }
  | { state: 'paused'; why: string }

export interface AgentRules {
  /** 还没读回来是 null，卡片这时不写状态。 */
  now: (id: string) => AgentRuleNow | null
  /** 做成了没有；没做成，卡片照旧。 */
  act: (id: string, action: 'cancel' | 'pause' | 'resume') => Promise<boolean>
}

export const agentRulesKey: InjectionKey<AgentRules> = Symbol('agentRules')

export function nowOf(rule: AssistantRoutine | undefined): AgentRuleNow {
  if (!rule) return { state: 'ended' }
  if (rule.state === 'paused') return { state: 'paused', why: rule.paused || 'person' }
  if (rule.state === 'done') return { state: rule.lastRun ? 'started' : 'gaveUp' }
  return { state: rule.once ? 'waiting' : 'on' }
}
