<script setup lang="ts">
// 一道题自己的看板 —— 「出题人要看得到自己这道题的情况」的落点。**真平台上原本没有
// 这一页**，它是这次新加的。
//
// 和整板看板的分工：整板看板回答「这个空间怎么样」（管理员的问题），这一页只回答
// 「**我这道题**怎么样」（出题人的问题）。所以这里没有跨题排行、没有分类分布，只有
// 这一道题的四件事：有多少人领、他们走到哪一步、卡在哪儿、什么时候动的。
//
// 数据来自三个真接口，没有一个数字是本地编的：
// - `GET /tasks/{id}` —— 题目本身
// - `GET /tasks/{id}/participants` —— 领取名单（含每人加入时刻，走势图就从它算）
// - `GET /spaces/{id}/submissions?taskId=…` —— 这道题的所有提交（含判没判、判过没过）
import type { Task, TaskMembership, TaskSubmissionReview } from '@/types'

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import BarList from '../components/BarList.vue'
import MetricCard from '../components/MetricCard.vue'
import PanelCard from '../components/PanelCard.vue'
import SplitBar from '../components/SplitBar.vue'
import TrendChart from '../components/TrendChart.vue'
import { isManager, me } from '../store'

import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'

type ClaimStatus = 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'

const CLAIM_LABEL: Record<ClaimStatus, string> = {
  IN_PROGRESS: '进行中',
  SUBMITTED: '已提交',
  PASSED: '已通过',
  REJECTED: '未通过',
}

const route = useRoute()
const spaceId = Number(route.params.spaceId)
const taskId = Number(route.params.taskId)

const task = ref<Task | null>(null)
const roster = ref<TaskMembership[]>([])
/** participantId → 这一条报名记录最新一版提交的评审结果（`undefined` = 交了还没判）。 */
const reviewByParticipant = ref(new Map<number, TaskSubmissionReview | undefined>())
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [taskRes, rosterRes, subsRes] = await Promise.all([
      TasksApi.detail(taskId),
      TasksApi.getParticipants(taskId),
      SpacesApi.getSubmissionQueue(spaceId, { taskId, pageSize: 200 }),
    ])
    task.value = taskRes.data.task
    roster.value = rosterRes.data.participants ?? []

    // 同一人可能有多版提交：按 version 取最大的那一版 —— 看的永远是「现在这一版判没判」。
    const byVersion = new Map<number, { version: number; review?: TaskSubmissionReview }>()
    for (const row of subsRes.data.submissions ?? []) {
      const prev = byVersion.get(row.participantId)
      if (!prev || row.version > prev.version) {
        byVersion.set(row.participantId, { version: row.version, review: row.review })
      }
    }
    reviewByParticipant.value = new Map([...byVersion.entries()].map(([pid, v]) => [pid, v.review]))
  } finally {
    loading.value = false
  }
}

load()

const publisherHandle = computed(() => task.value?.creator?.username ?? '')
const canManage = computed(() => isManager.value || publisherHandle.value === me.value.handle)

/** 没领到 / 没权限的人看到的是一句说明，不是一张空表（真接口也会对无权的人 403）。 */
const claimedRoster = computed(() => roster.value.filter((r) => r.approved !== 'DISAPPROVED'))

function statusOf(participantId: number): ClaimStatus {
  const review = reviewByParticipant.value.get(participantId)
  if (review === undefined) {
    // 有提交记录但没评审结果 —— 交了，等判。
    return reviewByParticipant.value.has(participantId) ? 'SUBMITTED' : 'IN_PROGRESS'
  }
  return review.detail?.accepted ? 'PASSED' : 'REJECTED'
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

const teamRows = computed(() => {
  const map = new Map<string, number>()
  for (const r of claimedRoster.value) {
    const key = r.teamMembers?.length ? '小队' : '单人'
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

/** 领取者名单，按加入时间倒序。 */
const ROSTER = computed(() => [...claimedRoster.value].sort((a, b) => b.createdAt - a.createdAt))

function daysAgo(ms: number) {
  const d = Math.max(0, Math.round((Date.now() - ms) / 86_400_000))
  return d === 0 ? '今天' : `${d} 天前`
}

function deadlineText(t: Task): string {
  if (t.deadline == null) return '不限截止'
  const days = Math.ceil((t.deadline - Date.now()) / 86_400_000)
  if (days < 0) return `已截止 ${-days} 天`
  if (days === 0) return '今天截止'
  return `${days} 天后截止`
}

function stillOpen(t: Task): boolean {
  return t.approved === 'APPROVED' && (t.deadline == null || t.deadline > Date.now())
}

function mineTo() {
  return { name: 'SpaceBoardMine', params: { spaceId: String(spaceId) } }
}
</script>

<template>
  <div v-if="task" class="ins">
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" :to="mineTo()" class="ins__back"> 回到我的 </v-btn>

    <div class="ins__head">
      <div>
        <h1>{{ task.name }}</h1>
        <p>
          {{ task.creator?.nickname || task.creator?.username }} 出题
          <template v-if="task.category?.name"> · {{ task.category.name }}</template>
          · {{ deadlineText(task) }} · {{ task.participantLimit ? `领取上限 ${task.participantLimit}` : '领取不限' }} ·
          {{
            (task.minTeamSize ?? 1) === 1 && (task.maxTeamSize ?? 1) === 1
              ? '单人'
              : `小队 ${task.minTeamSize}–${task.maxTeamSize} 人`
          }}
        </p>
      </div>
      <v-chip variant="tonal" :color="stillOpen(task) ? 'success' : 'default'" label>
        {{ stillOpen(task) ? '还可以领' : '已停止领取' }}
      </v-chip>
    </div>

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

      <PanelCard class="ins__span2" title="领取者" :subtitle="`${totalClaims} 人 · 按领取时间倒序`">
        <ul v-if="ROSTER.length" class="roster">
          <li v-for="m in ROSTER" :key="m.id">
            <v-avatar size="26" class="roster__avatar">{{ (m.member?.name || '?').slice(0, 1) }}</v-avatar>
            <span class="roster__name">{{ m.member?.name }}</span>
            <span v-if="m.teamMembers?.length" class="roster__team">小队 {{ m.teamMembers.length }} 人</span>
            <v-spacer />
            <span class="roster__at">{{ daysAgo(m.createdAt) }}领</span>
            <v-chip size="x-small" label variant="tonal" :class="`claim-${statusById.get(m.id)?.toLowerCase()}`">
              {{ CLAIM_LABEL[statusById.get(m.id) ?? 'IN_PROGRESS'] }}
            </v-chip>
          </li>
        </ul>
        <v-empty-state v-else icon="mdi-account-outline" title="还没有人领取" />
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
.ins__back {
  margin-bottom: 10px;
}

.ins__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 18px;
}

.ins__head h1 {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 650;
  line-height: 1.4;
}

.ins__head p {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.58);
  font-size: 0.8rem;
}

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
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
  line-height: 1.7;
}

.roster {
  padding: 0;
  margin: 0;
  list-style: none;
}

.roster li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.roster li:first-child {
  border-top: none;
}

.roster__avatar {
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.roster__name {
  font-size: 0.85rem;
}

.roster__team {
  padding: 1px 8px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.roster__at {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.74rem;
}

.claim-in_progress {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.claim-submitted {
  color: rgb(var(--v-theme-warning));
}

.claim-passed {
  color: rgb(var(--v-theme-success));
}

.claim-rejected {
  color: rgb(var(--v-theme-error));
}

.ins__guard {
  margin-top: 16px;
}
</style>
