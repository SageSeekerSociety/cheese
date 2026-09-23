<script setup lang="ts">
// 我的。两块：「我发布的」和「我领取的」。
//
// 这一页是**要求 4 里「出题人要看得到自己这道题的情况」的主入口** —— 但入口只是
// 概览，真要看细的走每道题自己的看板（/insights/:id）。把两者分开是有原因的：
// 概览回答「我出的题整体怎么样」，单题看板回答「这一道卡在哪儿」，塞进一页会都不清楚。
import { computed, ref, watch } from 'vue'

import MetricCard from '../components/MetricCard.vue'
import PageBar from '../components/PageBar.vue'
import PanelCard from '../components/PanelCard.vue'
import { CLAIM_LABEL, deadlineText } from '../fixtures'
import { isManager, me, myClaim, myClaimed, myPublished } from '../store'

/** 一页 20 条，与题目板同口径。出题多的人（比如所有者）这里会翻好几页。 */
const PAGE_SIZE = 20

const tab = ref<'published' | 'claimed'>(myPublished.value.length ? 'published' : 'claimed')
const page = ref(1)

const pagedPublished = computed(() => myPublished.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const pagedClaimed = computed(() => myClaimed.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 两个页签共用一套页码：切页签时回到第 1 页，否则会落在另一份列表的空页上。
watch(tab, () => (page.value = 1))
watch(
  () => Math.max(1, Math.ceil((tab.value === 'published' ? myPublished.value : myClaimed.value).length / PAGE_SIZE)),
  (last) => {
    if (page.value > last) page.value = last
  }
)

const pubStats = computed(() => {
  const list = myPublished.value
  const claims = list.reduce((n, t) => n + t.claims.length, 0)
  const submitted = list.reduce((n, t) => n + t.submitted, 0)
  const passed = list.reduce((n, t) => n + t.passed, 0)
  const pending = list.filter((t) => t.state === 'PENDING').length
  return {
    tasks: list.length,
    claims,
    pending,
    completion: submitted ? Math.round((passed / submitted) * 100) : 0,
    // 「还在动」= 有领取、没到截止。用来回答「我这题是不是凉了」。
    live: list.filter((t) => t.state === 'PUBLISHED' && bj(t)).length,
  }
})

function bj(t: { deadline: string }) {
  return new Date(t.deadline).getTime() > Date.now()
}

function fill(t: { claims: unknown[]; participantLimit: number | null }) {
  if (t.participantLimit === null) return null
  return Math.round((t.claims.length / t.participantLimit) * 100)
}
</script>

<template>
  <div class="mine">
    <div class="mine__head">
      <h1>我的</h1>
      <p>{{ me.name }} 在这块板上的东西。</p>
    </div>

    <v-tabs v-model="tab" density="comfortable" class="mine__tabs">
      <v-tab value="published">我发布的（{{ myPublished.length }}）</v-tab>
      <v-tab value="claimed">我领取的（{{ myClaimed.length }}）</v-tab>
    </v-tabs>

    <!-- 我发布的 -->
    <div v-if="tab === 'published'" class="mine__pane">
      <div v-if="myPublished.length" class="mine__kpis">
        <MetricCard label="我出的题" :value="pubStats.tasks" icon="mdi-file-document-outline" hint="含待审与已驳回" />
        <MetricCard
          label="累计被领取"
          :value="pubStats.claims"
          icon="mdi-hand-extended-outline"
          hint="已上板题目的领取次数"
        />
        <MetricCard
          label="等审核"
          :value="pubStats.pending"
          icon="mdi-clock-outline"
          :tone="pubStats.pending ? 'warn' : 'muted'"
          :hint="pubStats.pending ? '还在队列里，审过才上板' : '没有卡在审核的'"
        />
        <MetricCard
          label="平均完成率"
          :value="`${pubStats.completion}%`"
          icon="mdi-progress-check"
          hint="已通过 / 已提交"
        />
      </div>

      <ul v-if="myPublished.length" class="pub-list">
        <li v-for="task in pagedPublished" :key="task.id" class="pub-row">
          <div class="pub-row__main">
            <div class="pub-row__titleline">
              <router-link :to="`/task/${task.id}`" class="pub-row__title">{{ task.title }}</router-link>
              <v-chip
                size="x-small"
                label
                variant="tonal"
                :class="task.state === 'PUBLISHED' ? 'tone-ok' : task.state === 'PENDING' ? 'tone-warn' : 'tone-danger'"
              >
                {{ task.state === 'PUBLISHED' ? '已上板' : task.state === 'PENDING' ? '待审核' : '已驳回' }}
              </v-chip>
            </div>
            <div class="pub-row__meta">
              <span>{{ task.category }}</span>
              <span>{{ deadlineText(task) }}</span>
              <span
                >{{ task.claims.length
                }}{{ task.participantLimit === null ? '' : ` / ${task.participantLimit}` }} 人领取</span
              >
              <span v-if="fill(task) !== null">{{ fill(task) }}% 满</span>
              <span>提交 {{ task.submitted }} · 通过 {{ task.passed }}</span>
            </div>
          </div>
          <div class="pub-row__actions">
            <v-btn
              size="small"
              variant="tonal"
              prepend-icon="mdi-chart-line"
              :to="`/insights/${task.id}`"
              :disabled="task.state !== 'PUBLISHED'"
            >
              看这道题的情况
            </v-btn>
          </div>
        </li>
      </ul>

      <v-empty-state
        v-else
        icon="mdi-lightbulb-on-outline"
        title="你还没出过题"
        text="这块板上任何人都能出题 —— 想到一道就发，审过就能被人领。"
      >
        <template #actions>
          <v-btn color="primary" variant="flat" to="/publish">出第一道题</v-btn>
        </template>
      </v-empty-state>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="myPublished.length" @update:page="page = $event" />
    </div>

    <!-- 我领取的 -->
    <div v-else class="mine__pane">
      <PanelCard v-if="myClaimed.length" title="我领取的题">
        <ul class="claim-list">
          <li v-for="task in pagedClaimed" :key="task.id">
            <router-link :to="`/task/${task.id}`" class="claim-list__title">{{ task.title }}</router-link>
            <span class="claim-list__by">{{ task.publisher.name }}</span>
            <v-spacer />
            <span v-if="myClaim(task)?.team" class="claim-list__team">{{ myClaim(task)?.team }}</span>
            <v-chip size="x-small" label variant="tonal" :class="`claim-${myClaim(task)?.status.toLowerCase()}`">
              {{ CLAIM_LABEL[myClaim(task)!.status] }}
            </v-chip>
            <span class="claim-list__due">{{ deadlineText(task) }}</span>
          </li>
        </ul>
      </PanelCard>
      <v-empty-state v-else icon="mdi-hand-extended-outline" title="你还没领过题" text="去题目板看看有没有想做的。">
        <template #actions>
          <v-btn color="primary" variant="flat" to="/">去题目板</v-btn>
        </template>
      </v-empty-state>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="myClaimed.length" @update:page="page = $event" />
    </div>

    <p v-if="!isManager" class="mine__foot">
      这是普通用户视角的「我的」。所有者和管理员还会多一个「数据看板」入口，看的是整块板而不只是自己出的题。
    </p>
  </div>
</template>

<style scoped lang="scss">
.mine__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.mine__head p {
  margin: 6px 0 14px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.85rem;
}

.mine__tabs {
  margin-bottom: 18px;
}

.mine__pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mine__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}

.pub-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.pub-row {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 12px;
}

.pub-row__titleline {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.pub-row__title {
  font-size: 0.94rem;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.pub-row__title:hover {
  text-decoration: underline;
}

.pub-row__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.77rem;
}

.tone-ok {
  color: rgb(var(--v-theme-success));
}

.tone-warn {
  color: rgb(var(--v-theme-warning));
}

.tone-danger {
  color: rgb(var(--v-theme-error));
}

.claim-list {
  padding: 0;
  margin: 0;
  list-style: none;
}

.claim-list li {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.claim-list li:first-child {
  border-top: none;
}

.claim-list__title {
  font-size: 0.9rem;
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.claim-list__title:hover {
  text-decoration: underline;
}

.claim-list__by,
.claim-list__due {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.76rem;
}

.claim-list__team {
  padding: 1px 8px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
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

.mine__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
}
</style>
