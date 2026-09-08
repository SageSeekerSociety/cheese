// 合并态 (#718) 的呈现规则 —— **这里没有一行在算状态**。
//
// state 和「谁的活」(who) 都由后端算好随卡下发 (backend domain/review/
// merge_state.py)；这个文件只把它们翻成卡面上的一个词和一个圈。圈用的是看板
// 「该谁动」的点语言 (lib/board.ts 的 columnDotStyle)：整套界面里只有这一种
// 颜色在回答「现在轮到谁」，卡上再造一套，看的人就得在脑子里做一次翻译。
import type { BoardColumn, MergeReason, MergeStateInfo } from '@/cx_types'

export interface MergeBadge {
  label: string
  /** 圈的样式键，喂给 lib/board.ts 的 columnDotStyle。 */
  column: BoardColumn
}

/** 状态行上的词 + 圈。
 *
 *  词优先按 state 分：dirty / behind / clean 这三个状态本身就说明了下一步是
 *  什么；unstable / blocked 才需要 who 来分「检查红了」和「CI 还在跑」——那个
 *  分流后端做过了（issue #718 的表格），这里照抄结论。 */
export function mergeBadgeOf(ms: MergeStateInfo): MergeBadge {
  switch (ms.state) {
    case 'clean':
      // 绿勾 + 采纳亮；圈是「等你」—— 下一步真的在人手上。平台 lane（没绑
      // GitHub）的卡恒是这一档或 dirty（#363 拍板：没有检查可读，卡直接 CLEAN）。
      return { label: '可以合并', column: 'needs_you' }
    case 'dirty':
      return { label: '芝士处理中', column: 'building' }
    case 'behind':
      return { label: '平台更新分支', column: 'delivering' }
    case 'unknown':
      // 绑了 GitHub 的卡 unknown 是「还没看过，下一拍收敛」。
      return { label: '状态更新中', column: 'delivering' }
    default:
      break
  }
  // unstable / blocked：按后端下发的「谁的活」分。
  switch (ms.who) {
    case 'agent':
      return { label: '芝士处理中', column: 'building' }
    case 'ci':
      return { label: '等 CI', column: 'delivering' }
    case 'human':
      // 采纳被新提交作废，或机器看不出细节 —— 球在人手上。
      return { label: '等采纳', column: 'needs_you' }
    default:
      return { label: '平台处理中', column: 'delivering' }
  }
}

/** 卡面上值得念出来的依据。`no_obstacle`（可以合并）和 `no_signal`（还没看）
 *  只是在复述状态词，念一遍是废话，滤掉。 */
export function visibleReasons(ms: MergeStateInfo): MergeReason[] {
  return ms.reasons.filter((r) => r.kind !== 'no_obstacle' && r.kind !== 'no_signal')
}
