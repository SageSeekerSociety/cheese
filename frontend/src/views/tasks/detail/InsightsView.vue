<script setup lang="ts">
// 「数据」页签的画面：这一道题有多少人领、他们走到哪一步、卡在哪儿、什么时候动的。
// 数字全部从 props 里的名单和提交算出来，取数在 `Insights.vue`。
import type { Task, TaskMembership, TaskSubmissionReview } from '@/types'

import { computed } from 'vue'

import BarList from '@/components/spaces/BarList.vue'
import MetricCard from '@/components/spaces/MetricCard.vue'
import PanelCard from '@/components/spaces/PanelCard.vue'
import SplitBar from '@/components/spaces/SplitBar.vue'
import TrendChart from '@/components/spaces/TrendChart.vue'

type ClaimStatus = 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'

const props = defineProps<{
  task: Task | null
  roster: TaskMembership[]
  /** participantId → 这一条报名记录最新一版提交的评审结果（`undefined` = 交了还没判）。 */
  reviewByParticipant: Map<number, TaskSubmissionReview | undefined>
  canManage: boolean
  loading: boolean
}>()

/** 没领到 / 没权限的人看到的是一句说明，不是一张空表（真接口也会对无权的人 403）。 */
const claimedRoster = computed(() => props.roster.filter((r) => r.approved !== 'DISAPPROVED'))

function statusOf(participantId: number): ClaimStatus {
  if (!props.reviewByParticipant.has(participantId)) return 'IN_PROGRESS'
  // 有提交但还没判 —— 接口对没判的那一版回 `{ reviewed: false }`，不是空，所以看 `reviewed`。
  const review = props.reviewByParticipant.get(participantId)
  if (!review?.reviewed) return 'SUBMITTED'
  return review.detail.accepted ? 'PASSED' : 'REJECTED'
}

const statusById = computed(() => {
  const map = new Map<number, ClaimStatus>()
  for (const r of claimedRoster.value) map.set(r.id, statusOf(r.id))
  return map
})

const counts = computed(() => {
  const c: Record<ClaimStatus, number> = { IN_PROGRESS: 0, SUBMITTED: 0, PASSED: 0, REJECTED: 0 }
  for (const s of statusById.value.values()) c[s] += 1
  return c
})

const totalClaims = computed(() => claimedRoster.value.length)
const submitted = computed(() => counts.value.SUBMITTED + counts.value.PASSED + counts.value.REJECTED)
const passed = computed(() => counts.value.PASSED)

/** 没人认领的时候所有比例都该是 0，不是 NaN —— 除零是这类页最常见的假数据。 */
const rate = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0)

const statusSegments = computed(() => [
  { label: '已通过', count: counts.value.PASSED, tone: 'ok' as const },
  { label: '已提交', count: counts.value.SUBMITTED, tone: 'warn' as const },
  { label: '进行中', count: counts.value.IN_PROGRESS, tone: 'muted' as const },
  { label: '未通过', count: counts.value.REJECTED, tone: 'danger' as const },
])

/** 「卡住的人」：领了但一步没动，而且领了超过 5 天。出题人最该盯的就是这一行。 */
const stalled = computed(() =>
  claimedRoster.value.filter(
    (r) => statusById.value.get(r.id) === 'IN_PROGRESS' && Date.now() - r.createdAt > 5 * 86_400_000
  )
)

/** 这条报名是不是一支团队领的。接口一直回 `isTeam`；老数据没有时才看名册那一列。 */
function isTeamClaim(r: TaskMembership): boolean {
  return r.isTeam ?? Boolean(r.teamMembers?.length)
}

/** 「小队构成」按队名分桶 —— 出题人要的是「哪几支队伍来了」，不是「有几支队伍」。 */
const teamRows = computed(() => {
  const map = new Map<string, number>()
  for (const r of claimedRoster.value) {
    const key = r.team?.name?.trim() || (isTeamClaim(r) ? '小队' : '单人')
    map.set(key, (map.get(key) ?? 0) + 1)
  }
  return [...map.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value)
})

/** 最近 12 天的累计领取 —— 从每人的加入时刻数出来，不是接口另给的一串数。 */
const DAY_LABELS = computed(() =>
  Array.from({ length: 12 }, (_, i) => {
    const d = new Date(Date.now() - (11 - i) * 86_400_000)
    return `${d.getMonth() + 1}/${d.getDate()}`
  })
)

/** 窗口开始之前就领了的人算作第 0 天的底数，否则走势会假装他们不存在。 */
const claimTrend = computed(() => {
  const start = Date.now() - 12 * 86_400_000
  const base = claimedRoster.value.filter((r) => r.createdAt < start).length
  return Array.from({ length: 12 }, (_, i) => {
    const dayEnd = start + (i + 1) * 86_400_000
    return base + claimedRoster.value.filter((r) => r.createdAt >= start && r.createdAt < dayEnd).length
  })
})
</script>

<template>
  <div v-if="task" class="ins">
    <div class="ins__kpis">
      <MetricCard
        label="领取人数"
        :value="task.participantLimit ? `${totalClaims} / ${task.participantLimit}` : `${totalClaims}`"
        icon="mdi-hand-extended-outline"
        :hint="task.participantLimit ? `还剩 ${Math.max(0, task.participantLimit - totalClaims)} 个名额` : '不限人数'"
      />
      <MetricCard label="已提交" :value="submitted" icon="mdi-tray-arrow-up" hint="含已通过的" />
      <MetricCard label="通过率" :value="`${rate(passed, submitted)}%`" icon="mdi-progress-check" hint="通过 / 提交" />
      <MetricCard
        label="领取后一直没动"
        :value="stalled.length"
        icon="mdi-alert-circle-outline"
        :tone="stalled.length ? 'warn' : 'muted'"
        :hint="stalled.length ? '领了 5 天以上还是「进行中」' : '没有长期停住的'"
      />
    </div>

    <div class="ins__grid">
      <PanelCard class="ins__span2" title="领取走势" subtitle="最近 12 天，累计领取人数">
        <TrendChart
          v-if="totalClaims"
          :labels="DAY_LABELS"
          :series="[{ name: '累计领取', values: claimTrend }]"
          :height="200"
        />
        <v-empty-state
          v-else
          icon="mdi-chart-timeline-variant"
          title="还没有领取数据"
          text="题目上板之后这里才会有走势。"
        />
      </PanelCard>

      <PanelCard title="大家走到哪一步了">
        <SplitBar :segments="statusSegments" />
        <p class="ins__note">
          通过率 {{ rate(passed, submitted) }}%，未通过 {{ counts.REJECTED }} 人。
          这个比例跟整个空间的平均比对，才看得出题目是偏难还是偏松。
        </p>
      </PanelCard>

      <PanelCard title="小队构成">
        <BarList :rows="teamRows" unit=" 人" empty="还没有人成组" />
      </PanelCard>
    </div>

    <!-- 只有出题人/管理员打得到这一页；打不到的人应该被告知为什么，而不是看到一张空表。 -->
    <v-alert v-if="!canManage" type="info" variant="tonal" class="ins__guard">
      这一页只对这道题的出题人本人和管理员开放。
    </v-alert>
  </div>

  <v-empty-state v-else-if="!loading" icon="mdi-help-circle-outline" title="找不到这道题" />
</template>

<style scoped lang="scss">
.ins__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.ins__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .ins__grid {
    grid-template-columns: 1fr;
  }
}

.ins__span2 {
  grid-column: span 2;
}

@media (max-width: 900px) {
  .ins__span2 {
    grid-column: span 1;
  }
}

.ins__note {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.ins__guard {
  margin-top: 16px;
}
</style>
