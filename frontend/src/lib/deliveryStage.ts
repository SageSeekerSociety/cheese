// 交付进度 (pr_open 止血) — pure helpers kept out of the view so they're
// testable without mounting Vuetify.
//
// A card in `pr_open` is past the human decision: someone clicked 采纳 and what
// is left is the PR's own checks, which run for minutes to hours.
//
// It used to have a second phase. The backend waited for a deploy workflow to
// succeed before archiving, so this file split `pr_open` in two on
// `pr_merged_at` and drew a four-step chain ending in 部署. #206 removed that
// gate — merged is the finish line, and "deployed" is a per-project ops concept
// the platform was in no position to define. A `pr_open` card is therefore
// always waiting on checks, and promising a 部署 step that will never light up
// is worse than not drawing it.
//
// Still one phase short of right: whether a project HAS external checks at all
// is something only the backend knows (an unlinked project has no CI, and #363
// says that is legitimate rather than a degradation). The next step is for the
// backend to send the steps it actually has and for this file to render them —
// at which point the derivation below disappears rather than growing a second
// special case.
import type { AcceptCard } from '../cx_types'

export type DeliveryPhase = 'ci'

export type DeliveryStepState = 'done' | 'active' | 'todo'

export interface DeliveryStep {
  key: 'accepted' | 'ci' | 'merge'
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
}

function steps(active: DeliveryStep['key']): DeliveryStep[] {
  const order: DeliveryStep['key'][] = ['accepted', 'ci', 'merge']
  const at = order.indexOf(active)
  return order.map((key, i) => ({
    key,
    label: STEP_LABELS[key],
    state: i < at ? 'done' : i === at ? 'active' : 'todo',
  }))
}

// Takes the card even though it no longer reads it: the argument is what the
// next step needs (the backend sending the steps this project actually has), and
// dropping it now would mean changing every call site twice.
export function deliveryStageOf(card: Pick<AcceptCard, 'pr_merged_at'>): DeliveryStage {
  void card
  return {
    phase: 'ci',
    title: '等 PR 检查通过',
    hint: '检查全绿后平台会自动合并，合并即完成；红了平台会叫芝士回这个话题修，改完自动重跑。',
    steps: steps('ci'),
  }
}

// 后端把交付途中的阶段信息写在卡的 `note` 上，并用 emoji 前缀区分严重程度
// （services.py: `⚠️` 检查未通过/轮询暂停、`🚫` GitHub 拒绝合并、`✋` 三个例外
// 之一命中、平台拒绝免人自动合并）。
// 这里只是把那个前缀翻成一个颜色，不解析文案。
export function deliveryNoteTone(note: string): 'error' | 'info' | null {
  const text = note.trim()
  if (!text) return null
  // `✋` 不是故障，但它跟前面几个一样是"停住了、等人动手"，按能见度算同一档——
  // 划到 info 里就会跟"CI 还在跑"长得一模一样，那正是这条安全阀要避免的事。
  if (['⚠️', '❌', '🚫', '✋'].some((p) => text.startsWith(p))) return 'error'
  return 'info'
}
