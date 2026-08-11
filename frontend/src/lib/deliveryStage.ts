// 交付进度 (pr_open 止血) — pure helpers kept out of the view so they're
// testable without mounting Vuetify.
//
// A card in `pr_open` is past the human decision: someone already clicked
// 采纳, and what's left (CI → merge → deploy → archive) is machine work that
// runs for hours. This module turns the card into something renderable.
//
// ⚠️ 唯一允许存在的技术债，且只允许存在于本文件里：后端把「等 CI」和「等部署」
// 两个阶段压在同一个 `pr_open` 状态上，靠 `pr_merged_at` 是否为空来区分（见
// backend/app/domain/review/models.py 里 AcceptStatus.pr_open 的注释）。
// `deliveryStageOf` 把那个推导复制进了前端 —— 代价是一个 `if`。
//
// **不要把 `pr_merged_at === null` 这个判断抄到模板、别的 computed 或别的组件
// 里。** 需要阶段信息就调本函数、读它的返回值。将来交付轨拆成独立状态后，改
// 的只是本文件的取数来源，渲染分支和文案原样保留。
import type { AcceptCard } from '../cx_types'

// 等 CI（PR 还没合）/ 等部署（PR 合了，部署工作流在跑）。
export type DeliveryPhase = 'ci' | 'deploy'

export type DeliveryStepState = 'done' | 'active' | 'todo'

export interface DeliveryStep {
  key: 'accepted' | 'ci' | 'merge' | 'deploy'
  label: string
  state: DeliveryStepState
}

export interface DeliveryStage {
  phase: DeliveryPhase
  title: string
  hint: string
  steps: DeliveryStep[]
}

const STEP_LABELS: Record<DeliveryStep['key'], string> = {
  accepted: '已采纳',
  ci: 'CI 检查',
  merge: '合并进 main',
  deploy: '部署',
}

function steps(active: DeliveryStep['key']): DeliveryStep[] {
  const order: DeliveryStep['key'][] = ['accepted', 'ci', 'merge', 'deploy']
  const at = order.indexOf(active)
  return order.map((key, i) => ({
    key,
    label: STEP_LABELS[key],
    state: i < at ? 'done' : i === at ? 'active' : 'todo',
  }))
}

export function deliveryStageOf(card: Pick<AcceptCard, 'pr_merged_at'>): DeliveryStage {
  // 👇 这就是那个 `if`。全前端只此一处。
  if (card.pr_merged_at === null) {
    return {
      phase: 'ci',
      title: '等 PR 检查通过',
      hint: '检查全绿后平台会自动合并；红了平台会叫芝士回这个话题修，改完自动重跑。',
      steps: steps('ci'),
    }
  }
  return {
    phase: 'deploy',
    title: '等部署完成',
    hint: 'PR 已合并进 main，部署也成功后话题才会归档。',
    steps: steps('deploy'),
  }
}

// 后端把交付途中的阶段信息写在卡的 `note` 上，并用 emoji 前缀区分严重程度
// （services.py: `⚠️` 检查未通过/轮询暂停、`❌` 部署失败、`🚫` GitHub 拒绝合并）。
// 这里只是把那个前缀翻成一个颜色，不解析文案。
export function deliveryNoteTone(note: string): 'error' | 'info' | null {
  const text = note.trim()
  if (!text) return null
  if (text.startsWith('⚠️') || text.startsWith('❌') || text.startsWith('🚫')) return 'error'
  return 'info'
}
