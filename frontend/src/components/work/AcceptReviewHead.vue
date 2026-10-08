<script setup lang="ts">
// 「改动」页顶部那块（`ChangesReviewHead`）的接线：页面取了一份采纳卡
// （`provideAcceptCard`），这里读同一份，和对话栏那一条看的是同一刻。没有待审阅的卡、
// 或者不在这样的页面里，这里什么都不画。
//
// 它站在 `components/panels/` 之外，理由同 `PanelChangesHost`：面板是场景，场景不读会
// 取数的东西。
import { injectAcceptCard } from '../../composables/useAcceptCard'
import ChangesReviewHead from '../accept/ChangesReviewHead.vue'

const accept = injectAcceptCard()
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
    :my-handle="accept.author"
    :agent-name="accept.agentName.value"
    :agent-handle="accept.agentHandle.value"
    :deliverable-busy="accept.deliverableBusy.value"
    :deliverable-error="accept.deliverableError.value"
    @approve="accept.onApproveCard"
    @reassign="accept.onReassignCard"
    @download="accept.onDownloadDeliverable"
    @void="accept.onVoidCard"
    @force-merge="accept.onForceMerge"
    @toggle-auto-merge="accept.onToggleAutoMerge"
  />
</template>
