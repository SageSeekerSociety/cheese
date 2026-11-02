// 台面上那张卡「是什么」：卡上的 note 说的是什么、合并态翻译成哪几个词、按钮亮不
// 亮、谁能改派给谁。全是读 card 得出来的，不碰网络也不改任何状态。
//
// 拆自 useAcceptCard.ts（#2143 从 1215 行的组件里拆出来时，注释就跟着它解释的那段
// 代码走）。卡本身在 acceptCard/cards.ts，按下去打到哪个端点在 acceptCard/actions.ts。
import type { ComputedRef } from 'vue'
import type { AcceptCard, MergeReason } from '@/cx_types'
import type { MergeBadge } from '@/lib/mergeState'
import type { useWorkspaceStore } from '@/stores/workspace'

import { computed } from 'vue'

import { useUserRef } from '@/composables/useUserRef'

import { t } from '@/i18n'
import { mergeBadgeOf, visibleReasons } from '@/lib/mergeState'
import { noteTone } from '@/lib/noteTone'

/** 派生要读的那几张卡，和它们旁边的两个人（谁在看、芝士叫什么）。 */
export interface AcceptCardDeriveHost {
  store: ReturnType<typeof useWorkspaceStore>
  /** 谁在看这张卡。默认是登录的那个账号；演示页没有登录的人，读者扮的是卡上点名的审阅人。 */
  me: ComputedRef<string>
  pendingCard: ComputedRef<AcceptCard | null>
  acceptedCard: ComputedRef<AcceptCard | null>
  deliveringCard: ComputedRef<AcceptCard | null>
}

export function useAcceptCardDerive(host: AcceptCardDeriveHost) {
  const { store, me, pendingCard, acceptedCard, deliveringCard } = host

  // 后端把途中的阶段信息/故障写在卡的 note 上（PR 有新提交、GitHub 拒绝合并、
  // 凭据失效），那是这些事唯一露头的地方，照原样显示。轻重由 note_level 定。
  const cardNote = (card: AcceptCard | null) => {
    if (!card) return null
    const tone = noteTone(card)
    return tone ? { text: card.note, tone } : null
  }
  const deliveryNote = computed(() => cardNote(deliveringCard.value))
  // 待采纳卡上的同一条 note（比如「PR 有新提交，之前看到的版本已过时」）。冲突卡
  // 的 note 已经在冲突说明里念过了，不再重复。
  const pendingNote = computed(() =>
    pendingCard.value && pendingCard.value.status !== 'conflict' ? cardNote(pendingCard.value) : null
  )

  // 卡上的状态 = 合并态 (#718)：词和「谁的活」的圈都是后端算好的，这里只翻译
  // （lib/mergeState.ts）。冲突卡的标题已经说了「芝士处理中」，不再画第二行。
  const mergeBadge = computed<MergeBadge | null>(() => {
    const card = pendingCard.value
    if (!card || card.status === 'conflict') return null
    return mergeBadgeOf(card.merge_state, store.agentName)
  })
  const needsPr = computed(() => {
    const card = pendingCard.value
    if (!card || !card.forge.hosts_proposals || card.pr_number !== null) return false
    if (card.deliverable?.kind === 'file' || card.deliverable?.kind === 'link') {
      return card.merge_state.state === 'unknown'
    }
    return true
  })
  const mergeReasons = computed<MergeReason[]>(() => {
    const card = pendingCard.value
    if (!card) return []
    return card.forge.reports_checks && needsPr.value ? card.merge_state.reasons : visibleReasons(card.merge_state)
  })
  // 读不到检查结论的托管方，采纳就是验收人自己的判断。
  const platformLane = computed(() => {
    const card = pendingCard.value
    return !!card && !card.forge.reports_checks
  })
  // 这个托管方在人点之前就说明了自己是谁（I23）；GitHub 那一档不说话，卡上有链接。
  const forgeDeclaration = computed(() => pendingCard.value?.forge.declaration || '')
  // 按钮亮不亮，跟后端的采纳闸门是同一条线（domain/review/merge_state.py +
  // services.py）：`clean` 与 `unstable` 后端会合，按钮就亮；`blocked` /
  // `behind` / `dirty` / `unknown` 后端会 422 拒，按钮就灰，title 说明为什么。
  // unstable 是「有检查没过，但没有一个在必跑名单上」——它可以合，而红了哪个检查
  // 照样念在按钮上方的依据行里（mergeReasons），亮着不等于不说。
  // 灰之前按住的那半条路（人工放行）也用这个判断，两个入口不许对「现在能不能合」
  // 有两种看法。
  const MERGEABLE_STATES = ['clean', 'unstable']
  const acceptBlockedTitle = computed<string | null>(() => {
    const card = pendingCard.value
    if (!card) return null
    // 托管方读不出来的那一档（forge.kind === 'unknown'）：能力位一位都不敢说是，所以
    // 它看起来像平台 lane，但它不是 —— 后端这会儿真去采纳会按同一个失败 422 拒掉。
    // 闸门和采纳是同一条线，那就灰在这里，理由用卡上已经写着的那一句。
    if (card.forge.kind === 'unknown') return card.forge.declaration
    if (platformLane.value || needsPr.value) return null
    if (MERGEABLE_STATES.includes(card.merge_state.state)) return null
    const why = mergeReasons.value.map((r) => r.detail).filter(Boolean)
    return [t('topic.accept.notMergingNow'), ...why].join(t('topic.accept.separator'))
  })

  // 绿了自动合 (#718)：项目允许、且卡正停在 blocked/behind（规则还没满足）时才有
  // 这个开关；已布防的开关一直可见，好让人解除。
  const autoMergeArmedBy = computed(() => pendingCard.value?.auto_merge.armed_by ?? null)
  const autoMergeVisible = computed(() => {
    const card = pendingCard.value
    if (!card || !card.auto_merge.allowed) return false
    const state = card.merge_state.state
    return state === 'blocked' || state === 'behind' || !!card.auto_merge.armed_by
  })

  // The bar line is a plain string and cannot hold a UserRef, so the person is named
  // here with the same display name a UserRef would draw (#2764); a handle the roster
  // does not know stays a handle.
  const reviewerName = useUserRef(() => pendingCard.value?.reviewer_handle).label
  const deciderName = useUserRef(() => acceptedCard.value?.decided_by).label

  // 横条上放不放「退回 / 采纳」：轮到人做决定、或者现在点采纳就能合进去时放。检查
  // 还在跑、芝士在修的时候按钮点了也合不进去，那一刻要人做的事是等，不是决定；真要
  // 在这时强行采纳，入口在「改动」页顶部的「更多操作」里。冲突卡留着「重新采纳」，
  // 平台托管的仓库没有检查可等，采纳纯是人的判断。
  // 采纳和退回只有卡上点名的审阅人能做（后端 `acceptReviewerOnly` / `returnReviewerOnly`）：
  // 别人看到的是「待某人审阅」，要接手就在「改动」页的「更多操作」里改由他人审阅。
  const decisionOpen = computed(() => {
    const card = pendingCard.value
    if (!card || card.reviewer_handle !== me.value) return false
    if (card.status === 'conflict' || platformLane.value || needsPr.value) return true
    return card.merge_state.who === 'human' || !acceptBlockedTitle.value
  })
  // 采纳按钮上的字。
  const acceptLabel = computed(() => {
    const card = pendingCard.value
    if (needsPr.value) return t('work.room.accept.createPr')
    if (card?.status === 'conflict') return t('work.room.accept.retryAccept')
    if (card?.completes_task === false) return t('work.room.accept.acceptAction')
    return t('work.room.accept.acceptAndComplete')
  })
  // 「仍要采纳」：明知合并态不是 clean 仍合并。只在按钮灰着、而挡住它的不是另一个
  // 还没合的任务时有。
  const forceMergeVisible = computed(() => {
    const card = pendingCard.value
    return !!card?.pr_number && !!acceptBlockedTitle.value && !mergeReasons.value.some((r) => r.kind === 'dependency')
  })

  // 能改派给谁：名册上的人。AI 队友不在单子上——审阅是人对 AI 交来的东西把关，协作
  // 模式下 AI 也采纳不了，改派给它卡就停在那儿没人收（后端同样拒绝，`reviewers.py`）。
  // 和开始前挑审阅人那张单子是同一个口径。
  const reviewerChoices = computed(() => store.members.filter((m) => !m.agent && m.active !== false))

  return {
    pendingNote,
    deliveryNote,
    mergeBadge,
    mergeReasons,
    platformLane,
    forgeDeclaration,
    needsPr,
    acceptBlockedTitle,
    autoMergeArmedBy,
    autoMergeVisible,
    reviewerName,
    deciderName,
    decisionOpen,
    acceptLabel,
    forceMergeVisible,
    reviewerChoices,
  }
}
