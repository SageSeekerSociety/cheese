import { t } from '@/i18n'

// 一个模型在选单上的样子。后端是唯一事实源（model_choices），这里不留第二份
// 清单。团队方案不允许的模型标出允许它的最便宜方案（方案名，不是档位），选不了。
export interface AgentFieldChoice {
  id: string
  label: string
  description: string
  default: boolean
  allowed: boolean
  /** 允许这个模型的最便宜方案的名字；方案已允许或没有方案可选时为 null */
  requires_plan: string | null
  /** 这个模型认的思考强度，按低到高；空＝只能用模型默认 */
  efforts?: string[]
}

// A saved model the catalog no longer offers. Turns refuse it rather than fall
// back, so the picker shows it as saved and unavailable instead of blank.
interface SavedUnavailable {
  id: string
  label: string
  unavailable: true
}

export function withSaved<T extends { id: string | null }>(
  choices: T[],
  saved: string | null | undefined
): (T | SavedUnavailable)[] {
  if (!saved || choices.some((choice) => choice.id === saved)) return choices
  return [{ id: saved, label: saved, unavailable: true }, ...choices]
}

// `v-select` 的 `item-props`：方案不允许的模型不能选，下方写明需要哪个方案。
// 「跟随项目主模型」那一项没有这两个字段，照常可选。
export function modelChoiceProps(item: object): Record<string, unknown> {
  const choice = item as Partial<AgentFieldChoice & SavedUnavailable>
  if (choice.unavailable) return { disabled: true, subtitle: t('work.models.unavailable') }
  if (choice.allowed !== false) return {}
  return {
    disabled: true,
    subtitle: choice.requires_plan
      ? t('work.models.requiresPlan', { plan: choice.requires_plan })
      : t('work.models.notInPlan'),
  }
}
