<script setup lang="ts">
/**
 * 后台的统计页（平台总览、性能、交付流水线、用量、产品指标、反馈趋势、集成状态）。
 * 七页同一个容器，路由用 `kind` 告诉它是哪一类（`router/feedback.ts` 的 `statsRoute`）。
 *
 * 这一层是容器：取数在 `useAdminStats`，画面在 `AdminStatsPageView.vue`，这里只接线。
 */
import type { StatsKind } from '@/lib/adminStats'

import { useRouter } from 'vue-router'

import { useAdminStats } from '@/composables/useAdminStats'

import { queue } from '@/lib/adminStats'
import AdminStatsPageView from '@/views/admin/AdminStatsPageView.vue'

defineOptions({ name: 'AdminStatsPage' })

const props = defineProps<{ kind: StatsKind }>()

const router = useRouter()

const {
  days,
  windowed,
  loading,
  error,
  failed,
  stampText,
  pending,
  listLoading,
  pipeline,
  product,
  feedback,
  usage,
  platform,
  performance,
  integrations,
  setDays,
  retry,
} = useAdminStats(props.kind)

/** 图上点某一天 → 队列看那一天收进来的。 */
function onSelectDay(date: string | null) {
  if (!date) return
  void router.push(queue({ since: date.slice(0, 10) }))
}
</script>

<template>
  <AdminStatsPageView
    :kind="kind"
    :windowed="windowed"
    :days="days"
    :stamp="stampText"
    :loading="loading"
    :failed="failed"
    :error="error"
    :pipeline="pipeline"
    :product="product"
    :feedback="feedback"
    :usage="usage"
    :platform="platform"
    :performance="performance"
    :integrations="integrations"
    :pending="pending"
    :list-loading="listLoading"
    @set-days="setDays"
    @retry="retry"
    @select-day="onSelectDay"
  />
</template>
