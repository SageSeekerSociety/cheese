<script setup lang="ts">
// 「改动」页顶部那块（`ChangesReviewHead`）的接线：页面取了一份采纳卡
// （`provideAcceptCard`），这里读同一份，和对话栏那一条看的是同一刻。没有待审阅的卡、
// 或者不在这样的页面里，这里什么都不画。
//
// 它站在 `components/panels/` 之外，理由同 `PanelChangesHost`：面板是场景，场景不读会
// 取数的东西。
import { computed } from 'vue'

import { injectAcceptCard } from '../../composables/useAcceptCard'
import ChangesReviewHead from '../accept/ChangesReviewHead.vue'

import { t } from '@/i18n'

const accept = injectAcceptCard()

// 上一轮退回带走的批注，芝士重新交上来之后才谈得上处理了几条。
const round = computed(() => {
  const c = accept?.comments
  if (!c || !c.roundAnswered.value) return []
  return c.lastRound.value
    .filter((r) => !r.parent_id)
    .map((r) => ({
      id: r.id,
      text: r.body || t('work.room.review.suggestion'),
      where: r.current_line === null ? t('work.room.review.gone') : `${r.path.split('/').pop()}:${r.current_line}`,
      outcome: r.outcome,
    }))
})
</script>

<template>
  <ChangesReviewHead
    v-if="accept?.pendingCard.value"
    v-model:show-void-input="accept.showVoidInput.value"
    v-model:void-note="accept.voidNote.value"
    v-model:show-force-merge-input="accept.showForceMergeInput.value"
    v-model:force-merge-reason="accept.forceMergeReason.value"
    :card="accept.pendingCard.value"
    :badge="accept.mergeBadge.value"
    :reasons="accept.mergeReasons.value"
    :forge-declaration="accept.forgeDeclaration.value"
    :note="accept.pendingNote.value"
    :pr-checks="accept.prChecks.value"
    :reviewer-choices="accept.reviewerChoices.value"
    :busy="accept.acceptBusy.value"
    :auto-merge-visible="accept.autoMergeVisible.value"
    :auto-merge-armed-by="accept.autoMergeArmedBy.value"
    :force-merge-visible="accept.forceMergeVisible.value"
    :my-handle="accept.author.value"
    :agent-name="accept.agentName.value"
    :agent-handle="accept.agentHandle.value"
    :deliverable-busy="accept.deliverableBusy.value"
    :deliverable-error="accept.deliverableError.value"
    :round="round"
    @approve="accept.onApproveCard"
    @reassign="accept.onReassignCard"
    @download="accept.onDownloadDeliverable"
    @void="accept.onVoidCard"
    @force-merge="accept.onForceMerge"
    @toggle-auto-merge="accept.onToggleAutoMerge"
  />
</template>
