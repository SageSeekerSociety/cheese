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
}

// `v-select` 的 `item-props`：方案不允许的模型不能选，下方写明需要哪个方案。
// 「跟随项目主模型」那一项没有这两个字段，照常可选。
export function modelChoiceProps(item: object): Record<string, unknown> {
  const choice = item as Partial<AgentFieldChoice>
  if (choice.allowed !== false) return {}
  return {
    disabled: true,
    subtitle: choice.requires_plan
      ? t('work.models.requiresPlan', { plan: choice.requires_plan })
      : t('work.models.notInPlan'),
  }
}
