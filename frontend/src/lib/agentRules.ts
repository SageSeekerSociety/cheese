// 芝士建的自动化在面板里要的东西：卡片上它此刻的样子、外壳提供给卡片的读写口，和它运行时
// 对话里平台那一行怎么说。
import type { InjectionKey } from 'vue'
import type { AssistantRoutine } from '@/api'
import type { AssistantShown } from '@/stores/agentThread'

import { t } from '@/i18n'

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

/** 平台开始一次运行的那一句：芝士读到的是平台的说明，人看到的是这一行——哪条自动化开始运行，
 *  或这段对话里等的事到了（任务的审阅时说是哪件事）。 */
export function runLine(shown?: AssistantShown): string {
  if (shown?.task) return t(`tasks.assistant.run.${shown.event ?? 'filed'}`, { task: shown.task })
  return t(`tasks.assistant.run.${shown?.wait ? 'wait' : 'routine'}`, { title: shown?.wait ?? shown?.routine ?? '' })
}
