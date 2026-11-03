// 验收卡上按下去会打到哪个端点：采纳、退回、作废、人工放行、改派、撤回采纳、布防
// 自动合并、下载这一版交出去的那一份。每一处都是「置忙 → 打端点 → 重新拉卡 → 报错」，
// 成功后屏幕上那份卡换成新的一版，不先整张消失再长回来（见 acceptCard/cards.ts 的
// `load`）。
//
// 展开在卡上的那几张小表单（退回理由、作废理由、人工放行的理由）是这些动作自己的
// 状态，跟着它们走。卡本身在 acceptCard/cards.ts，卡面上的判断在 acceptCard/derive.ts。
import type { ComputedRef } from 'vue'
import type { useReviewComments } from '@/composables/useReviewComments'
import type { AcceptCard } from '@/cx_types'
import type { useWorkspaceStore } from '@/stores/workspace'

import { ref } from 'vue'

import { noteAccepted } from '@/composables/useInvitePrompt'

import {
  acceptCard,
  approveCard,
  cardDeliverableUrl,
  downloadFile,
  mergeCardAnyway,
  reassignCard,
  rejectCard,
  revokeCard,
  setAutoMerge,
  voidCard,
} from '@/api'
import { t } from '@/i18n'

/** 动作要读的卡、它旁边的两个人、以及重新拉一次的那只手。 */
export interface AcceptCardActionsHost {
  store: ReturnType<typeof useWorkspaceStore>
  /** 拿它去刷新主题行（采纳/退回之后那一下就靠这个把话题状态带回来）。读成函数，话题会换。 */
  topicId: () => string
  /** 谁在看这张卡。默认是登录的那个账号；演示页没有登录的人，读者扮的是卡上点名的审阅人。 */
  me: ComputedRef<string>
  pendingCard: ComputedRef<AcceptCard | null>
  acceptedCard: ComputedRef<AcceptCard | null>
  /** 这张卡的托管方不代递 PR：采纳是当场开一个，失败时的话不一样。 */
  needsPr: ComputedRef<boolean>
  /** 这件任务的批注：退回时随卡送出。 */
  comments: ReturnType<typeof useReviewComments>
  /** 重新拉一次这个任务的卡；`alongside` 见 acceptCard/cards.ts 的说明。 */
  reload: (alongside?: Promise<unknown>) => Promise<void>
}

export function useAcceptCardActions(host: AcceptCardActionsHost) {
  const { store, me, pendingCard, acceptedCard, needsPr, comments, reload } = host

  const acceptBusy = ref(false)
  const rejectNote = ref('')
  const showRejectInput = ref(false)
  // 作废：卡停在一个没人能推进的地方（GitHub 拒绝合并、冲突卡等）时的出口。
  // 它不是退回——不叫芝士改，只结束这次审阅，所以同样要先展开、再确认。
  const voidNote = ref('')
  const showVoidInput = ref(false)
  // 人工放行 (#718): 明知合并态不是 clean 仍合并。默认拒绝、显式放行，所以
  // 它藏在一个要先展开、再填理由的小表单后面——不是一个可以顺手点到的按钮。
  const showForceMergeInput = ref(false)
  const forceMergeReason = ref('')

  // 这一版交出去的那一份，在人点采纳之前拿到手 (#1085 结论五)。走下载而不是预览：
  // 给的是递卡那一刻落下的快照，要审的就是这些字节本身。
  const deliverableBusy = ref(false)
  async function onDownloadDeliverable() {
    const card = pendingCard.value
    const filename = card?.deliverable?.filename
    if (!card || !filename || deliverableBusy.value) return
    deliverableBusy.value = true
    try {
      // 成功和失败各弹一句，都在 `downloadFile` 那一层（`lib/download`）：卡上的
      // 「拿不到那份」那行红字撤掉了，失败的信息不再留在卡里。
      await downloadFile(cardDeliverableUrl(card.id), filename)
    } finally {
      deliverableBusy.value = false
    }
  }

  // 主分支保护 (spec §4.4): my vote toward the pending card's accept.
  async function onApproveCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await approveCard(card.id, me.value)
      await reload()
    } catch (e) {
      store.reportError(e, t('topic.accept.approveFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onReassignCard(handle: string) {
    const card = pendingCard.value
    if (!card || handle === card.reviewer_handle) return
    acceptBusy.value = true
    try {
      await reassignCard(card.id, handle)
      await reload()
    } catch (e) {
      store.reportError(e, t('topic.accept.reassignFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onAcceptCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      const updated = await acceptCard(card.id, me.value, card.merge_state.head_sha, card.deliverable?.revision ?? null)
      if (updated.status === 'conflict') {
        store.error = t('topic.accept.conflict', { agent: store.agentName })
      }
      if (updated.status === 'accepted') void noteAccepted()
      // 卡已经定了：开着的退回框和里面那半句理由不再适用。
      showRejectInput.value = false
      rejectNote.value = ''
      await reload(store.refreshTopicRow(host.topicId()))
    } catch (e) {
      store.reportError(e, needsPr.value ? t('topic.accept.createPrFailed') : t('topic.accept.acceptFailed'))
      // 被拒的原因可能正是「你看到的版本已过时」——那就把屏幕换成新的那一版，
      // 否则人只能对着同一张旧卡再点一次，再被拒一次。
      await reload()
    } finally {
      acceptBusy.value = false
    }
  }

  async function onRevokeCard() {
    const card = acceptedCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await revokeCard(card.id, me.value)
      // 卡已经定了：开着的退回框和里面那半句理由不再适用。
      showRejectInput.value = false
      rejectNote.value = ''
      await reload(store.refreshTopicRow(host.topicId()))
    } catch (e) {
      store.reportError(e, t('topic.accept.undoFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onForceMerge() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await mergeCardAnyway(card.id, forceMergeReason.value, card.merge_state.head_sha)
      showForceMergeInput.value = false
      forceMergeReason.value = ''
      await reload(store.refreshTopicRow(host.topicId()))
    } catch (e) {
      store.reportError(e, t('topic.accept.acceptFailed'))
      await reload() // 见 onAcceptCard：过时的那一版要换掉
    } finally {
      acceptBusy.value = false
    }
  }

  // 绿了自动合 (#718)：布防/解除都打同一个端点，布防人由后端从会话认定。
  async function onToggleAutoMerge(enabled: unknown) {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await setAutoMerge(card.id, !!enabled, card.merge_state.head_sha)
      await reload()
    } catch (e) {
      store.reportError(e, t('topic.accept.autoMergeFailed'))
      await reload() // 见 onAcceptCard：过时的那一版要换掉
    } finally {
      acceptBusy.value = false
    }
  }

  async function onRejectCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await rejectCard(
        card.id,
        me.value,
        rejectNote.value,
        comments.toSend.value.map((c) => c.id)
      )
      showRejectInput.value = false
      rejectNote.value = ''
      await reload(store.refreshTopicRow(host.topicId()))
      await comments.load()
    } catch (e) {
      store.reportError(e, t('topic.accept.returnFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  async function onVoidCard() {
    const card = pendingCard.value
    if (!card) return
    acceptBusy.value = true
    try {
      await voidCard(card.id, voidNote.value)
      showVoidInput.value = false
      voidNote.value = ''
      await reload(store.refreshTopicRow(host.topicId()))
    } catch (e) {
      store.reportError(e, t('topic.accept.voidFailed'))
    } finally {
      acceptBusy.value = false
    }
  }

  // 换了话题（或任务）：这几张小表单收回原样。卡本身由 cards.ts 的 `reset` 收。
  function resetForms() {
    showRejectInput.value = false
    rejectNote.value = ''
    showVoidInput.value = false
    voidNote.value = ''
    showForceMergeInput.value = false
    forceMergeReason.value = ''
  }

  return {
    acceptBusy,
    rejectNote,
    showRejectInput,
    voidNote,
    showVoidInput,
    showForceMergeInput,
    forceMergeReason,
    deliverableBusy,
    onDownloadDeliverable,
    onApproveCard,
    onReassignCard,
    onAcceptCard,
    onRevokeCard,
    onForceMerge,
    onToggleAutoMerge,
    onRejectCard,
    onVoidCard,
    resetForms,
  }
}
