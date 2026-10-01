<script setup lang="ts">
// 六个要人管的数：前三个点进「题目」那一格并带上对应的筛选，后三个只是提醒（没有
// 可以直接跳去处理的地方）。总览和「告警」两处用同一份。
import type { SpaceAnalyticsAlerts } from '@/network/api/spaces/types'
import type { SpaceAnalyticsQueryState } from '../utils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { formatCount } from '../helpers'

const props = defineProps<{ alerts: SpaceAnalyticsAlerts }>()

const emit = defineEmits<{ open: [patch: Partial<SpaceAnalyticsQueryState>] }>()

const { t } = useI18n()

const items = computed(() => {
  const a = props.alerts
  return [
    { key: 'pendingTasks', value: a.pendingTaskApprovalCount, open: { taskApproved: 'NONE' } as const },
    { key: 'pendingClaims', value: a.pendingParticipantApprovalCount, open: { hasPendingApproval: true } },
    { key: 'pendingReviews', value: a.pendingSubmissionReviewCount, open: { hasPendingReview: true } },
    { key: 'stalledTasks', value: a.stalledTaskCount },
    { key: 'overdueReviews', value: a.overdueUnreviewedSubmissionCount },
    { key: 'inactivePublishers', value: a.inactivePublisherCount },
  ].map((item) => ({
    ...item,
    label: t(`spaces.analytics.alerts.${item.key}.label`),
    hint: t(`spaces.analytics.alerts.${item.key}.hint`),
    count: formatCount(item.value),
  }))
})
</script>

<template>
  <div class="alert-grid">
    <component
      :is="item.open ? 'button' : 'div'"
      v-for="item in items"
      :key="item.key"
      :type="item.open ? 'button' : undefined"
      class="alert"
      :class="{ 'alert--open': item.open }"
      @click="item.open && emit('open', item.open)"
    >
      <span class="alert__head">
        <span class="alert__label">{{ item.label }}</span>
        <v-icon v-if="item.open" size="16" class="alert__go">mdi-arrow-top-right</v-icon>
      </span>
      <span class="alert__value t-num">{{ item.count }}</span>
      <span class="alert__hint">{{ item.hint }}</span>
    </component>
  </div>
</template>

<style scoped>
.alert-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.alert {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  text-align: left;
}

.alert--open {
  cursor: pointer;
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}

.alert--open:hover {
  border-color: var(--line-2);
  background: var(--fill);
}

.alert--open:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}

.alert__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.alert__label {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.alert__go {
  color: var(--faint);
}

.alert__value {
  color: var(--ink);
  font-size: 23px;
  font-weight: 650;
  line-height: var(--lh-23);
}

.alert__hint {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
