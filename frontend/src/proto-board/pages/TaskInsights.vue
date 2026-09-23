<script setup lang="ts">
// 一道题自己的看板 —— 要求 4 里「出题人要看得到自己这道题的情况」的落点。
//
// 和整板看板的分工：整板看板回答「这块板怎么样」（管理员的问题），这一页只回答
// 「**我这道题**怎么样」（出题人的问题）。所以这里没有跨题排行、没有分类分布，
// 只有这一道题的四件事：有多少人领、他们走到哪一步、卡在哪儿、什么时候动的。
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import BarList from '../components/BarList.vue'
import MetricCard from '../components/MetricCard.vue'
import PanelCard from '../components/PanelCard.vue'
import SplitBar from '../components/SplitBar.vue'
import TrendChart from '../components/TrendChart.vue'
import { CLAIM_LABEL, DAY_LABELS, deadlineText, isOpen } from '../fixtures'
import { canManageTask, tasks } from '../store'

const route = useRoute()
const task = computed(() => tasks.value.find((t) => t.id === String(route.params.id)))

/** 没人认领的时候所有比例都该是 0，不是 NaN —— 除零是这类页最常见的假数据。 */
const rate = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0)

const statusSegments = computed(() => {
  const t = task.value
  if (!t) return []
  const counts = { IN_PROGRESS: 0, SUBMITTED: 0, PASSED: 0, REJECTED: 0 }
  for (const c of t.claims) counts[c.status] += 1
  return [
    { label: '已通过', count: counts.PASSED, tone: 'ok' as const },
    { label: '已提交', count: counts.SUBMITTED, tone: 'warn' as const },
    { label: '进行中', count: counts.IN_PROGRESS, tone: 'muted' as const },
    { label: '未通过', count: counts.REJECTED, tone: 'danger' as const },
  ]
})

/** 「卡住的人」：领了但一步没动，而且领了超过 5 天。出题人最该盯的就是这一行。 */
const stalled = computed(() => {
  const t = task.value
  if (!t) return []
  return t.claims.filter(
    (c) => c.status === 'IN_PROGRESS' && Date.now() - new Date(c.at).getTime() > 5 * 86_400_000,
  )
})

const teamRows = computed(() => {
  const t = task.value
  if (!t) return []
  const map = new Map<string, number>()
  for (const c of t.claims) {
    const key = c.team ?? '单人'
    map.set(key, (map.get(key) ?? 0) + 1)
  }
  return [...map.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value)
})

/** 领取发生在一天里的哪个时段。这张图回答的是「题目发出去之后多久热起来」。 */
const ROSTER = computed(() =>
  [...(task.value?.claims ?? [])].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()),
)

function daysAgo(iso: string) {
  const d = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000))
  return d === 0 ? '今天' : `${d} 天前`
}
</script>

<template>
  <div v-if="task" class="ins">
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" to="/mine" class="ins__back">回到我的</v-btn>

    <div class="ins__head">
      <div>
        <h1>{{ task.title }}</h1>
        <p>
          {{ task.publisher.name }} 出题 · {{ task.category }} · {{ deadlineText(task) }} ·
          {{ task.participantLimit === null ? '领取不限' : `领取上限 ${task.participantLimit}` }} ·
          {{ task.minTeamSize === 1 && task.maxTeamSize === 1 ? '单人' : `小队 ${task.minTeamSize}–${task.maxTeamSize} 人` }}
        </p>
      </div>
      <v-chip variant="tonal" :color="isOpen(task) ? 'success' : 'default'" label>
        {{ isOpen(task) ? '还可以领' : '已停止领取' }}
      </v-chip>
    </div>

    <div class="ins__kpis">
      <MetricCard
        label="领取人数"
        :value="task.participantLimit === null ? `${task.claims.length}` : `${task.claims.length} / ${task.participantLimit}`"
        icon="mdi-hand-extended-outline"
        :hint="task.participantLimit === null ? '不限人数' : `还剩 ${Math.max(0, task.participantLimit - task.claims.length)} 个名额`"
      />
      <MetricCard label="已提交" :value="task.submitted" icon="mdi-tray-arrow-up" hint="含已通过的" />
      <MetricCard label="通过率" :value="`${rate(task.passed, task.submitted)}%`" icon="mdi-progress-check" hint="通过 / 提交" />
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
          v-if="task.claimTrend.length"
          :labels="DAY_LABELS"
          :series="[{ name: '累计领取', values: task.claimTrend }]"
          :height="200"
        />
        <v-empty-state v-else icon="mdi-chart-timeline-variant" title="还没有领取数据" text="题目上板之后这里才会有走势。" />
      </PanelCard>

      <PanelCard title="大家走到哪一步了">
        <SplitBar :segments="statusSegments" />
        <p class="ins__note">
          通过率 {{ rate(task.passed, task.submitted) }}%，未通过 {{ task.claims.filter((c) => c.status === 'REJECTED').length }} 人。
          这个比例跟整块板的平均比对，才看得出题目是偏难还是偏松。
        </p>
      </PanelCard>

      <PanelCard title="小队构成">
        <BarList :rows="teamRows" unit=" 人" empty="还没有人成组" />
      </PanelCard>

      <PanelCard class="ins__span2" title="领取者" :subtitle="`${task.claims.length} 人 · 按领取时间倒序`">
        <ul v-if="ROSTER.length" class="roster">
          <li v-for="c in ROSTER" :key="c.handle">
            <v-avatar size="26" class="roster__avatar">{{ c.name.slice(0, 1) }}</v-avatar>
            <span class="roster__name">{{ c.name }}</span>
            <span v-if="c.team" class="roster__team">{{ c.team }}</span>
            <v-spacer />
            <span class="roster__at">{{ daysAgo(c.at) }}领</span>
            <v-chip size="x-small" label variant="tonal" :class="`claim-${c.status.toLowerCase()}`">
              {{ CLAIM_LABEL[c.status] }}
            </v-chip>
          </li>
        </ul>
        <v-empty-state v-else icon="mdi-account-outline" title="还没有人领取" />
      </PanelCard>
    </div>

    <!-- 只有出题人/管理员打得到这一页；打不到的人应该被告知为什么，而不是看到一张空表。 -->
    <v-alert v-if="!canManageTask(task)" type="info" variant="tonal" class="ins__guard">
      这一页只对这道题的出题人本人和管理员开放。
    </v-alert>
  </div>

  <v-empty-state v-else icon="mdi-help-circle-outline" title="找不到这道题" />
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
