<script setup lang="ts">
// 看板这一页**画的那一半**：页头、分类导轨、每一屏的数。
//
// 取数（分类的缓存、窗口、轮询、时效戳、重试）在容器 `AdminDashboardPage.vue`
// 和它调用的 `composables/useAdminDashboard.ts` 里；「图上点一天跳队列」那条接线
// 也留在容器里。这里只吃 props、只往上发事件，所以它能被单独挂起来看（A 档场景）。
import type {
  PendingRow,
  PulseRow,
  StatsDays,
  StatsFeedback,
  StatsIntegrations,
  StatsKind,
  StatsPerformance,
  StatsPipeline,
  StatsPlatform,
  StatsProduct,
  StatsUsage,
} from '@/lib/adminStats'

import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import AdminDashboardFeedback from '@/components/admin/dashboard/AdminDashboardFeedback.vue'
import AdminDashboardHeader from '@/components/admin/dashboard/AdminDashboardHeader.vue'
import AdminDashboardIntegrations from '@/components/admin/dashboard/AdminDashboardIntegrations.vue'
import AdminDashboardKinds from '@/components/admin/dashboard/AdminDashboardKinds.vue'
import AdminDashboardPerformance from '@/components/admin/dashboard/AdminDashboardPerformance.vue'
import AdminDashboardPipeline from '@/components/admin/dashboard/AdminDashboardPipeline.vue'
import AdminDashboardPlatform from '@/components/admin/dashboard/AdminDashboardPlatform.vue'
import AdminDashboardProduct from '@/components/admin/dashboard/AdminDashboardProduct.vue'
import AdminDashboardUsage from '@/components/admin/dashboard/AdminDashboardUsage.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

defineProps<{
  kinds: StatsKind[]
  tabs: Record<string, string>
  titles: Record<string, string>
  pulse: Record<string, PulseRow>
  kind: StatsKind
  days: StatsDays
  windowed: boolean
  loading: boolean
  error: string | null
  failed: boolean
  stamp: string
  pending: PendingRow[]
  listLoading: boolean
  pipeline: StatsPipeline | null
  product: StatsProduct | null
  feedback: StatsFeedback | null
  usage: StatsUsage | null
  platform: StatsPlatform | null
  performance: StatsPerformance | null
  integrations: StatsIntegrations | null
}>()

defineEmits<{
  setDays: [days: StatsDays]
  select: [kind: StatsKind]
  retry: []
  selectDay: [date: string | null]
}>()

const { t } = useI18n()
</script>

<template>
  <AdminPage :title="t('navigation.admin.dashboard')" :sub="t('feedback.dashboard.sub')">
    <template #tools>
      <AdminDashboardHeader :windowed="windowed" :days="days" :stamp="stamp" @set-days="$emit('setDays', $event)" />
    </template>
    <template #extra>
      <AdminDashboardKinds
        :kinds="kinds"
        :tabs="tabs"
        :titles="titles"
        :pulse="pulse"
        :current="kind"
        @select="$emit('select', $event)"
      />
    </template>

    <div class="ad__body admin-page__body">
      <!-- 错误是**整块**的（§9.3）：页头留着 —— 它是这一页的名字，不是数据。错误
             正文是**服务端原话**（不改写），重试是唯一主操作，而且真重拉 —— 不是把
             错误状态清掉装没事。块换成了共用的 `BaseEmptyState`（和队列、模型页的
             出错态同一个形状），这一页不再自己画一套 `ad__none-*`。 -->
      <BaseLoadError
        v-if="failed"
        :title="t('feedback.dashboard.error.title')"
        :error="error ?? undefined"
        :retry-label="t('feedback.dashboard.retry')"
        @retry="$emit('retry')"
      />

      <!-- 一屏一类。取数在容器那一半，画法在各屏自己那里。 -->
      <AdminDashboardPipeline v-else-if="kind === 'pipeline'" :data="pipeline" :loading="loading" />
      <AdminDashboardProduct v-else-if="kind === 'product'" :data="product" :days="days" :loading="loading" />
      <AdminDashboardIntegrations v-else-if="kind === 'integrations'" :data="integrations" :loading="loading" />
      <AdminDashboardFeedback
        v-else-if="kind === 'feedback'"
        :data="feedback"
        :pending="pending"
        :list-loading="listLoading"
        :days="days"
        :loading="loading"
        @select-day="$emit('selectDay', $event)"
      />
      <AdminDashboardUsage v-else-if="kind === 'usage'" :data="usage" :days="days" :loading="loading" />
      <AdminDashboardPerformance v-else-if="kind === 'performance'" :data="performance" :loading="loading" />
      <AdminDashboardPlatform v-else :data="platform" :days="days" :loading="loading" />
    </div>
  </AdminPage>
</template>

<style scoped>
/* 各屏第一块（指标条）自带 16px 上外边距，正文不再加一层。 */
.ad__body {
  padding-top: 0;
}
</style>
