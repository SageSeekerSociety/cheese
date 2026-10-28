/**
 * 验收卡（`TopicAcceptCard`）拆出来的那几件：卡本身、它那个 PR 的检查、几档历史
 * 状态，以及「改动」页顶部那块要吃的那一大把 props。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：卡取自剧本第四步（`./common` 的
 * `frame`），这一册只改它的几格。条目在 `../catalogAccept.ts`。
 */
import type { AcceptCard, PrChecks, ProjectMemberRow } from '@/cx_types'

import { answer } from '../demoBackend'

import { AGENT_NAME, frame } from './common'

import { mergeBadgeOf, visibleReasons } from '@/lib/mergeState'

/** 验收卡那一格：卡和它那个 PR 的检查（剧本第四步出现）。 */
export const ACCEPT_CARD = frame(4).card
export const ACCEPT_CHECKS = frame(4).checks

/**
 * 预览站里会自己取数的那几件（验收卡读卡和读检查）该读到什么。装到演示后端上
 * （`demoBackend`），和 `DemoRoom.installPanelAnswers` 是同一件事。
 */
export function installCatalogAnswers(): void {
  // The card is read through its task's own conversation.
  const conversation = ACCEPT_CARD?.task_id ?? 'demo'
  answer(`/topics/${conversation}/accept-card`, () => ({
    data: ACCEPT_CARD ? [ACCEPT_CARD] : [],
    total: ACCEPT_CARD ? 1 : 0,
  }))
  answer(`/topics/${conversation}/pr-checks`, () => ACCEPT_CHECKS ?? { available: false })
}

// 拆开之后 `components/accept/` 里这几件都是「只吃 props、只往上发事件」的那种
// （`frontend_grade.py` 的 A 级）：横条、退回那一块、「改动」页顶部那块，几件零件，
// 以及两张只读的历史脸。于是每一件都能单独摆进预览站，条目在 `catalogAccept.ts`。
// 数据就着剧本第四步那张卡改几格（`ACCEPT_CARD` / `ACCEPT_CHECKS`）——它们本来就是
// 同一张卡上的几段。

/** 那张卡（`AcceptPrChecks` / 几张脸都要）。剧本第四步一定有它（`installCatalogAnswers`
 *  也是照着它答的），所以这里就是那一张。 */
export const ACCEPT_ONE = ACCEPT_CARD as AcceptCard

/** 一份 PR 检查。起点是剧本里那份（`pr_number` / `state` / `mergeable` 都在），只换
 *  这一格要看的 `checks` 那一列：`status` / `conclusion` 就是 GitHub 那两个字段，
 *  `AcceptPrChecks` 照它们挑图标、写「进行中」。 */
export function acceptChecks(checks: NonNullable<PrChecks['checks']>, over: Partial<PrChecks> = {}): PrChecks {
  return { ...(ACCEPT_CHECKS ?? { available: true }), checks, ...over }
}

/** 卡上那条 note（`AcceptNoteLine`）的两种口气。句子是后端写的原话
 *  （`backend/app/domain/review/services.py`），前端只照 `note_level` 挑颜色和图标，
 *  所以这里抄的是那两句本身，不是一个「差不多」的文案。 */
export const ACCEPT_NOTE_ERROR = '这个项目的代码托管凭据不可用'
export const ACCEPT_NOTE_INFO = 'PR #1 有新提交，已有的采纳批准被作废'

/** 闸门未过（`gate_failed`）的历史卡：检查跑了、代码红了。输出抄的是检查命令真会
 *  写的那一行 —— `TopicAcceptCardGate.test.ts` 里也是这一行。 */
export const ACCEPT_GATE_FAILED: AcceptCard = {
  ...ACCEPT_ONE,
  status: 'gate_failed',
  gate_output: 'ruff: E501 line too long',
}
/** 闸门没跑成（`gate_blocked`）：检查本身没起来，对代码没有结论。这是「要人看一眼」
 *  的状态，和上面那张刻意分开。 */
export const ACCEPT_GATE_BLOCKED: AcceptCard = {
  ...ACCEPT_ONE,
  status: 'gate_blocked',
  gate_output: 'docker: not found',
}
/** 已采纳等合并（`pr_open`，#718 退役的兜底脸）：采纳过、合并没走完，只读。 */
export const ACCEPT_DELIVERING_ONE: AcceptCard = { ...ACCEPT_ONE, status: 'pr_open', decided_by: 'wang' }
/** 主分支保护：这次改动要两个人批准，李甘已经批了。`approvals` 记的是 handle
 *  （剧本里 `li` 就是李甘）。 */
export const ACCEPT_APPROVALS: AcceptCard = { ...ACCEPT_ONE, approvals: ['li'], approvals_required: 2 }
/** 检查还没绿：后端此刻会拒掉采纳，所以按钮灰着、`acceptBlockedTitle` 有话要说，
 *  旁边那颗「检查通过后自动合并」也只在 `blocked` / `behind` 这一档才出现。
 *  依据抄的是验收剧本里那条（`kind: ci_running` 那一格）。 */
export const ACCEPT_BLOCKED: AcceptCard = {
  ...ACCEPT_ONE,
  merge_state: {
    ...ACCEPT_ONE.merge_state,
    state: 'blocked',
    who: 'ci',
    reasons: [{ kind: 'ci_running', checks: ['CI required'], detail: '检查进行中' }],
  },
  auto_merge: { allowed: true, armed_by: null, armed_at: null },
}
/** 与主分支冲突（`conflict`）：芝士正在处理，这一支连状态词那一行都不画。 */
export const ACCEPT_CONFLICTED: AcceptCard = { ...ACCEPT_ONE, status: 'conflict', note: '' }

/** 「改由谁审阅」那一份名单：名册上还在岗的人（`useAcceptCard.ts` 就是拿 store 里的
 *  `members` 直接给菜单的）。 */
export const ACCEPT_REVIEWERS: ProjectMemberRow[] = [
  { user_handle: 'wang', name: '王长鑫', role: 'owner', source: 'owner' },
  { user_handle: 'li', name: '李甘', role: 'member', source: 'team' },
]

/**
 * `ChangesReviewHead`（「改动」页顶部那块）要吃的那一大把 props。默认是「可以采纳」那
 * 一档（剧本第四步那张卡），要看别的档就换一两个键就行。
 */
export function acceptHeadProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  const card = (over.card as AcceptCard | undefined) ?? ACCEPT_ONE
  return {
    card,
    // 冲突卡不画合并那个信号（下面那句已经说了芝士在处理），别的卡按合并态翻译。
    badge: card.status === 'conflict' ? null : mergeBadgeOf(card.merge_state, AGENT_NAME),
    reasons: visibleReasons(card.merge_state),
    forgeDeclaration: card.forge.declaration,
    note: null,
    prChecks: ACCEPT_CHECKS,
    reviewerChoices: ACCEPT_REVIEWERS,
    busy: false,
    autoMergeVisible: false,
    autoMergeArmedBy: null,
    forceMergeVisible: false,
    myHandle: 'wang',
    agentName: AGENT_NAME,
    agentHandle: 'cheese',
    deliverableBusy: false,
    // 四个 `defineModel` 是 required（它们的初值属于调用方的状态，重新读卡要能收起来）。
    // 预览站给的是「两个确认框都关着」。
    showVoidInput: false,
    voidNote: '',
    showForceMergeInput: false,
    forceMergeReason: '',
    ...over,
  }
}
