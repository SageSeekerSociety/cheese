<script setup lang="ts">
// 频道里审阅一件交付时那一格的页头（`DeliveryHead`）的接线：工作面板在审阅模式里取了这件
// 任务的一份采纳卡（`provideAcceptCard`），这里读同一份——页头上的退回 / 采纳、「改动」页
// 顶部、退回那一块看的是同一刻。审阅人已经决定了的，页头写结果（已采纳 / 已退回），和任务
// 页上一样。
//
// 它站在 `components/panels/` 之外，理由同 `AcceptReviewHead`：面板是场景，场景不读会取数的
// 东西。
import { computed } from 'vue'

import { injectAcceptCard } from '../../composables/useAcceptCard'
import DeliveryHead from '../room/DeliveryHead.vue'

defineProps<{
  heading: string
  version?: number | null
}>()

const emit = defineEmits<{ (e: 'open-task'): void }>()

const accept = injectAcceptCard()

// 这件任务最近递的那一张卡有了结果：页头写结果。
const decided = computed<'accepted' | 'returned' | null>(() => {
  if (!accept || accept.pendingCard.value) return null
  const latest = [...accept.acceptCards.value].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  if (latest?.status === 'accepted' || latest?.status === 'pr_open') return 'accepted'
  if (latest?.status === 'rejected') return 'returned'
  return null
})
const decide = computed(() => !!accept && !accept.gateCard.value && accept.decisionOpen.value)
</script>

<template>
  <DeliveryHead
    :heading="heading"
    :version="version ?? null"
    :decided="decided"
    :decide="decide"
    :accept-label="accept?.acceptLabel.value ?? ''"
    :blocked-title="accept?.acceptBlockedTitle.value ?? null"
    :busy="accept?.acceptBusy.value ?? false"
    @open-task="emit('open-task')"
    @accept="accept?.onAcceptCard()"
    @reject="accept && (accept.showRejectInput.value = true)"
  />
</template>
