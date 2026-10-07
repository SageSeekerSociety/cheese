<script setup lang="ts">
import type {
  PendingRow,
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
import AdminDashboardPerformance from '@/components/admin/dashboard/AdminDashboardPerformance.vue'
import AdminDashboardPipeline from '@/components/admin/dashboard/AdminDashboardPipeline.vue'
import AdminDashboardPlatform from '@/components/admin/dashboard/AdminDashboardPlatform.vue'
import AdminDashboardProduct from '@/components/admin/dashboard/AdminDashboardProduct.vue'
import AdminDashboardUsage from '@/components/admin/dashboard/AdminDashboardUsage.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

// 后台统计页的画面（取数在 `AdminStatsPage` → `useAdminStats`）。一页一类，`kind` 决定画
// 哪一屏、页名是什么。
//
// 数字口径有两处是刻意的：
//
//   * **存量与窗口分开**。`counts` 是全量（现在一共多少条），`series` 是窗口内的
//     （这七天怎么变的）—— 「现在是多少」和「这七天怎么走的」是两个问题，合成一个数
//     就会有一个是错的。窗口（7/30/90 天）由 store 的 `statsDays` 记，几页共用。
//   * **未定价的 token 单独算**。模型没有单价时，行上的 `cost_usd = 0.0` 意思是
//     「没有单价」而不是「免费」；把它们加进总额，会在几百万 token 上印一个
//     `$0.0000`，读起来像「这个月没花钱」。
//
// 每个数字要么写得出点下去去哪，要么说得出**为什么不能点**。机器那四个数是说得出理由
// 的例外：平台里没有一页列设备，它们点不进去，所以那一块只报数、不装成链接。
//
// 口径注脚一律收进 `AdminNoteTip`（标题旁的 info 图标 + tooltip），防误读级的常驻注每屏
// 至多一条（平台的 machines.note、性能的 perf.note）。
defineOptions({ name: 'AdminStatsPageView' })

defineProps<{
  kind: StatsKind
  /** 这一类有没有「过去 N 天」（没有就不画窗口切换器）。 */
  windowed: boolean
  days: StatsDays
  /** 「更新于 HH:MM」。 */
  stamp: string
  loading: boolean
  /** 这一类什么都没拿到、且有一句服务端原话。 */
  failed: boolean
  error: string | null
  pipeline: StatsPipeline | null
  product: StatsProduct | null
  feedback: StatsFeedback | null
  usage: StatsUsage | null
  platform: StatsPlatform | null
  performance: StatsPerformance | null
  integrations: StatsIntegrations | null
  /** 反馈趋势页右侧「需处理」那十行与它的加载态。 */
  pending: PendingRow[]
  listLoading: boolean
}>()

const emit = defineEmits<{
  setDays: [days: StatsDays]
  retry: []
  /** 反馈趋势图上点了某一天。去哪由容器决定。 */
  selectDay: [date: string | null]
}>()

const { t } = useI18n()

/** 页名。键名字面量写全（i18n 闸门照源码字面量认「有人在用」）。 */
const TITLE_KEY: Record<StatsKind, string> = {
  platform: 'navigation.admin.overview',
  performance: 'navigation.admin.performance',
  pipeline: 'navigation.admin.pipeline',
  usage: 'navigation.admin.usage',
  product: 'navigation.admin.product',
  feedback: 'navigation.admin.feedbackTrends',
  integrations: 'navigation.admin.integrationHealth',
}
</script>

<template>
  <AdminPage :title="t(TITLE_KEY[kind])">
    <template #tools>
      <AdminDashboardHeader :windowed="windowed" :days="days" :stamp="stamp" @set-days="emit('setDays', $event)" />
    </template>

    <div class="ad__body admin-page__body">
      <!-- 错误是**整块**的：页头留着 —— 它是这一页的名字，不是数据。错误正文是服务端
           原话（不改写），重试是唯一主操作，而且真重拉。 -->
      <BaseLoadError
        v-if="failed"
        :title="t('feedback.dashboard.error.title')"
        :error="error ?? undefined"
        :retry-label="t('feedback.dashboard.retry')"
        @retry="emit('retry')"
      />

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
        @select-day="emit('selectDay', $event)"
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
