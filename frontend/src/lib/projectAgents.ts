// 「AI 队友」设置页上不放进组件里的那几样：随机名字、名字和标识的校验、类型的显示名、
// 思考强度的档位与叫法。单独成文件，由测试直接盯着，而不是埋在组件里靠渲染结果间接验证。
import type { AgentEffort, AgentType } from '../cx_types'

import { t } from '../i18n'

export function randomTeammateName(current = ''): string {
  const names = [
    t('work.teammate.names.moss'),
    t('work.teammate.names.spark'),
    t('work.teammate.names.milo'),
    t('work.teammate.names.bean'),
    t('work.teammate.names.nova'),
    t('work.teammate.names.cedar'),
  ]
  const choices = names.filter((name) => name !== current)
  return choices[Math.floor(Math.random() * choices.length)]!
}

// 每个队友攒下了多少条记忆，按 handle 归。
//
// 一个队友用的是哪个类型。类型可能已经被删掉，或者目录本身没读到 —— 那时
// 只有名字可用，不该因此让整行显示成「通用」，那是另一件事。
export function findType(types: AgentType[], name: string | null | undefined): AgentType | null {
  if (!name) return null
  return types.find((t) => t.name === name) ?? null
}

export function typeLabel(types: AgentType[], name: string | null | undefined): string {
  if (!name) return t('work.projectSettings.agents.typeGeneral')
  return findType(types, name)?.title || name
}

// handle 是记忆池的键，也会出现在 URL 里，所以取值受限：小写字母/数字开头，
// 之后可跟 `.` `_` `-`，最长 64。和后端 `_HANDLE_RE` 同一套规则 —— 前端先拦一道，
// 是为了让人当场看见哪里不对，而不是提交后收一个 422。
const HANDLE_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/

export function handleError(handle: string): string | null {
  if (!handle) return null // 留空 = 由后端按名字/类型生成
  return HANDLE_RE.test(handle) ? null : t('work.projectSettings.agents.editor.handleInvalid')
}

export function displayNameError(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return t('work.projectSettings.agents.editor.nameRequired')
  if (trimmed.length > 64) return t('work.projectSettings.agents.editor.nameTooLong', { max: 64 })
  return null
}

// 思考强度，从低到高；和后端 `AgentConfiguration.effort`、网关 `cheese_efforts` 同一套词。
export const EFFORT_LEVELS: AgentEffort[] = ['low', 'medium', 'high', 'max']

const EFFORT_KEYS: Record<AgentEffort, string> = {
  low: 'work.projectSettings.agents.editor.effortLow',
  medium: 'work.projectSettings.agents.editor.effortMedium',
  high: 'work.projectSettings.agents.editor.effortHigh',
  max: 'work.projectSettings.agents.editor.effortMax',
}

export function effortLabel(effort: AgentEffort | null | undefined): string {
  return effort ? t(EFFORT_KEYS[effort]) : t('work.projectSettings.agents.editor.effortAuto')
}
