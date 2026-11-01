// 对话栏横条上那一行字、那个圈，以及「有没有东西可画」。台面上同一时刻只摆一张
// 卡，所以这几件事都先问「现在是哪一张在台面上」，再问那张卡说什么。
//
// 拆自 useAcceptCard.ts（#2143 从 1215 行的组件里拆出来时，注释就跟着它解释的那段
// 代码走）。卡在 acceptCard/cards.ts，卡面上的判断在 acceptCard/derive.ts。
import type { ComputedRef } from 'vue'
import type { AcceptCard } from '@/cx_types'
import type { MergeBadge } from '@/lib/mergeState'
import type { CardPhase } from '@/lib/topicState'

import { computed } from 'vue'

import { t } from '@/i18n'

/** 台面上那几张卡，加上画横条要用的两个名字和一个「芝士叫什么」。 */
export interface AcceptCardBarHost {
  /** 谁在看这张卡。默认是登录的那个账号；演示页没有登录的人，读者扮的是卡上点名的审阅人。 */
  me: ComputedRef<string>
  /** 项目里那个 AI 队友的显示名（store.agentName）。 */
  agentName: ComputedRef<string>
  /** 话题的状态：只有归档的话题才摆「撤回采纳」。读成函数，`hasBox` 跟着它变。 */
  topicStatus: () => string
  pendingCard: ComputedRef<AcceptCard | null>
  acceptedCard: ComputedRef<AcceptCard | null>
  deliveringCard: ComputedRef<AcceptCard | null>
  gateCard: ComputedRef<AcceptCard | null>
  mergeBadge: ComputedRef<MergeBadge | null>
  /** 卡上那个人的显示名（derive.ts 的 reviewerName / deciderName）。 */
  reviewerName: ComputedRef<string>
  deciderName: ComputedRef<string>
}

export function useAcceptCardBar(host: AcceptCardBarHost) {
  const { me, agentName, pendingCard, acceptedCard, deliveringCard, gateCard, mergeBadge, reviewerName, deciderName } =
    host

  // 横条上那一行说什么。顺序和 TopicAcceptCard 里的 v-if 链一致：同一时刻只有一张卡在
  // 台面上。待审阅的卡只说状态：交的是什么、检查怎样都在「改动」页顶部，对话栏窄的时候
  // 一句标题会被截得只剩几个字。
  const bar = computed<{ icon: string; color: string; title: string }>(() => {
    const gate = gateCard.value
    if (gate?.status === 'gate_failed')
      return { icon: 'mdi-close-octagon-outline', color: 'error', title: t('work.room.accept.gateFailed') }
    if (gate?.status === 'gate_blocked')
      return { icon: 'mdi-help-circle-outline', color: 'warning', title: t('work.room.accept.gateBlocked') }
    const pending = pendingCard.value
    if (pending?.status === 'conflict')
      return {
        icon: 'mdi-source-merge',
        color: 'warning',
        title: t('work.room.accept.conflict', { agent: agentName.value }),
      }
    if (pending)
      return {
        icon: 'mdi-source-merge',
        color: 'success',
        // 「待某人审阅」只在球真的在人手上（后端算的 who 是 human）时说：检查还在跑、
        // 芝士在修、平台在更新分支时，说的是合并态那个词。
        title:
          mergeBadge.value && pending.merge_state.who !== 'human'
            ? mergeBadge.value.label
            : pending.reviewer_handle === me.value
              ? t('work.room.accept.waitingOnYou')
              : t('work.room.accept.waitingOn', { name: reviewerName.value }),
      }
    if (deliveringCard.value) return { icon: 'mdi-history', color: 'warning', title: t('work.room.accept.delivering') }
    const accepted = acceptedCard.value
    return {
      icon: 'mdi-check-circle-outline',
      color: 'success',
      title: accepted
        ? t(accepted.decided_by_assistant ? 'work.room.accept.decidedByAssistant' : 'work.room.accept.decidedBy', {
            name: deciderName.value,
          })
        : t('work.room.accept.accepted'),
    }
  })

  // 横条上那个圈：和看板「该谁动」同一套点（lib/board.ts）。
  const barColumn = computed(() => {
    const pending = pendingCard.value
    if (!pending) return null
    if (pending.status === 'conflict') return 'building' as const
    if (pending.merge_state.who === 'human') return 'needs_you' as const
    return mergeBadge.value?.column ?? 'needs_you'
  })

  // Nothing to show at all — the host still renders the slot wrapper, so this
  // component simply contributes no box.
  const hasBox = computed(
    () =>
      !!(
        pendingCard.value ||
        gateCard.value ||
        deliveringCard.value ||
        (host.topicStatus() === 'archived' && acceptedCard.value)
      )
  )

  // 决策在聊天，审查在面板: where the topic stands is not this box's private
  // business. The header states it and the panel opens on the tab it calls for,
  // so the one word travels up rather than the card list travelling out.
  const phase = computed<CardPhase>(() => {
    if (deliveringCard.value) return 'delivering'
    if (gateCard.value) return 'gate'
    if (pendingCard.value) return 'pending'
    return null
  })

  return { bar, barColumn, hasBox, phase }
}
