// 交付进度 — the wording around the steps the BACKEND sends.
//
// This file used to derive the chain: it split `pr_open` on `pr_merged_at` and
// drew a fixed 已采纳 → CI → 合并 → 部署. Both halves of that were wrong for
// anyone but this repo. Whether a project deploys is its own ops business
// (#206 took it out of the accept), and whether it has external checks at all
// is a property of its forge (#363: an unlinked project is a repo with no CI,
// which is legitimate) — neither is visible from the browser.
//
// So the steps arrive on the card as `stages` and this file only says what they
// mean. The old comment here warned not to copy its `pr_merged_at` check
// anywhere else, "将来交付轨拆成独立状态后，改的只是本文件的取数来源" — this is
// that change, and the derivation is gone rather than moved.
import type { AcceptCard, DeliveryStep } from '../cx_types'

export type { DeliveryStep }

export interface DeliveryStage {
  title: string
  hint: string
  steps: DeliveryStep[]
}

/** The stage to render, or null when nothing is in flight. */
export function deliveryStageOf(card: Pick<AcceptCard, 'stages'>): DeliveryStage | null {
  const steps = card.stages ?? []
  if (steps.length === 0) return null
  const active = steps.find((s) => s.state === 'active')
  if (active?.key === 'checks') {
    return {
      title: '等待检查通过',
      hint: '检查全部通过后自动合并并完成；未通过时芝士会回到本话题修复，修复后自动重新检查',
      steps,
    }
  }
  return {
    title: '等待合并',
    hint: '本项目没有外部检查，合并即完成',
    steps,
  }
}

// 后端把交付途中的阶段信息/故障写在卡的 `note` 上，并用 emoji 前缀区分严重程度
// （services.py: `⚠️` 检查未通过/轮询暂停、`🚫` GitHub 拒绝合并、`✋` 三个例外
// 之一命中、平台拒绝免人自动合并）。这里只把前缀翻成一个颜色，不解析文案。
export function deliveryNoteTone(note: string): 'error' | 'info' | null {
  const text = note.trim()
  if (!text) return null
  // `✋` 不是故障，但它跟前面几个一样是"停住了、等人动手"，按能见度算同一档——
  // 划到 info 里就会跟"检查还在跑"长得一模一样，那正是这条安全阀要避免的事。
  if (['⚠️', '❌', '🚫', '✋'].some((p) => text.startsWith(p))) return 'error'
  return 'info'
}
