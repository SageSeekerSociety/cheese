<script setup lang="ts">
// 我的。两块：「我发布的」和「我领取的」。
//
// 这一页是**「出题人要看得到自己这道题的情况」的主入口** —— 但入口只是概览，
// 真要看细的走每道题自己的看板。把两者分开是有原因的：概览回答「我出的题整体
// 怎么样」，单题看板回答「这一道卡在哪儿」。
//
// 用的是真平台**专为这页准备的那两组接口**（`/spaces/{id}/me/publishing*` 与
// `/me/participating*`），不是从全板列表里筛 —— 那两条路会给「谁在等我审」
// 「多少人卡在我这」这类**只有出题人看得到**的数，全板列表里没有。
import type {
  AnalyticsCompletionType,
  SpaceMyParticipatingOverview,
  SpaceMyParticipation,
  SpaceMyPublishedTask,
  SpaceMyPublishingOverview,
} from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import MetricCard from '../components/MetricCard.vue'
import PageBar from '../components/PageBar.vue'
import PanelCard from '../components/PanelCard.vue'
import { isManager, me } from '../store'

import { SpacesApi } from '@/network/api/spaces'

/** 一页 20 条，与空间首页同口径。出题多的人（比如所有者）这里会翻好几页。 */
const PAGE_SIZE = 20

const route = useRoute()
const spaceId = Number(route.params.spaceId)

const tab = ref<'published' | 'claimed'>('published')
const page = ref(1)

const publishing = ref<SpaceMyPublishingOverview | null>(null)
const publishedTasks = ref<SpaceMyPublishedTask[]>([])
const participating = ref<SpaceMyParticipatingOverview | null>(null)
const participations = ref<SpaceMyParticipation[]>([])

async function refresh() {
  const [overview, tasks, partOverview, parts] = await Promise.all([
    SpacesApi.getMyPublishingOverview(spaceId),
    SpacesApi.getMyPublishedTasks(spaceId),
    SpacesApi.getMyParticipatingOverview(spaceId),
    SpacesApi.getMyParticipations(spaceId),
  ])
  publishing.value = overview.data
  publishedTasks.value = tasks.data.tasks ?? []
  participating.value = partOverview.data
  participations.value = parts.data.participations ?? []
}

refresh().then(() => {
  if (!publishedTasks.value.length && participations.value.length) tab.value = 'claimed'
})

const pagedPublished = computed(() => publishedTasks.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
const pagedClaimed = computed(() => participations.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 两个页签共用一套页码：切页签时回到第 1 页，否则会落在另一份列表的空页上。
watch(tab, () => (page.value = 1))
watch(
  () =>
    Math.max(
      1,
      Math.ceil((tab.value === 'published' ? publishedTasks.value : participations.value).length / PAGE_SIZE)
    ),
  (last) => {
    if (page.value > last) page.value = last
  }
)

/** 状态 → 文案与语气。上板与驳回之外，还有「过审了但过了截止日」。 */
function publishedState(t: SpaceMyPublishedTask): { text: string; tone: string } {
  if (t.visibilityStatus === 'PENDING_APPROVAL') return { text: '待审核', tone: 'tone-warn' }
  if (t.visibilityStatus === 'REJECTED') return { text: '已驳回', tone: 'tone-danger' }
  if (t.visibilityStatus === 'ENDED') return { text: '已截止', tone: 'tone-muted' }
  return { text: '已上板', tone: 'tone-ok' }
}

const COMPLETION_LABEL: Record<AnalyticsCompletionType, string> = {
  NOT_SUBMITTED: '进行中',
  PENDING_REVIEW: '待判',
  REJECTED_RESUBMITTABLE: '未通过',
  FAILED: '未通过',
  SUCCESS: '已通过',
}

function completionClass(status: AnalyticsCompletionType) {
  if (status === 'SUCCESS') return 'claim-passed'
  if (status === 'NOT_SUBMITTED') return 'claim-in_progress'
  if (status === 'PENDING_REVIEW') return 'claim-submitted'
  return 'claim-rejected'
}

function deadlineText(ms: number | null | undefined): string {
  if (ms == null) return '不限截止'
  const days = Math.ceil((ms - Date.now()) / 86_400_000)
  if (days < 0) return `已截止 ${-days} 天`
  if (days === 0) return '今天截止'
  return `${days} 天后截止`
}

function detailTo(taskId: number) {
  return { name: 'SpaceBoardTaskDetail', params: { spaceId: String(spaceId), taskId: String(taskId) } }
}

function insightsTo(taskId: number) {
  return { name: 'SpaceBoardTaskInsights', params: { spaceId: String(spaceId), taskId: String(taskId) } }
}

function publishTo() {
  return { name: 'SpaceBoardTaskPublish', params: { spaceId: String(spaceId) } }
}

function homeTo() {
  return { name: 'SpaceBoardHome', params: { spaceId: String(spaceId) } }
}
</script>

<template>
  <div class="mine">
    <div class="mine__head">
      <h1>我的</h1>
      <p>{{ me.name }} 在这个空间里的东西。</p>
    </div>

    <v-tabs v-model="tab" density="comfortable" class="mine__tabs">
      <v-tab value="published">我发布的（{{ publishedTasks.length }}）</v-tab>
      <v-tab value="claimed">我领取的（{{ participations.length }}）</v-tab>
    </v-tabs>

    <!-- 我发布的 -->
    <div v-if="tab === 'published'" class="mine__pane">
      <div v-if="publishing" class="mine__kpis">
        <MetricCard
          label="我出的题"
          :value="publishing.taskCount"
          icon="mdi-file-document-outline"
          hint="含待审与已驳回"
        />
        <MetricCard
          label="累计被领取"
          :value="publishing.participantCount"
          icon="mdi-hand-extended-outline"
          hint="已上板题目的领取次数"
        />
        <MetricCard
          label="等审核"
          :value="publishing.pendingTaskApprovalCount"
          icon="mdi-clock-outline"
          :tone="publishing.pendingTaskApprovalCount ? 'warn' : 'muted'"
          :hint="publishing.pendingTaskApprovalCount ? '还在队列里，审过才上板' : '没有卡在审核的'"
        />
        <MetricCard
          label="等我判"
          :value="publishing.pendingReviewCount"
          icon="mdi-clipboard-check-outline"
          :tone="publishing.pendingReviewCount ? 'warn' : 'muted'"
          hint="有人交了作业还没判"
        />
      </div>

      <ul v-if="publishedTasks.length" class="pub-list">
        <li v-for="task in pagedPublished" :key="task.taskId" class="pub-row">
          <div class="pub-row__main">
            <div class="pub-row__titleline">
              <router-link :to="detailTo(task.taskId)" class="pub-row__title">{{ task.taskName }}</router-link>
              <v-chip size="x-small" label variant="tonal" :class="publishedState(task).tone">
                {{ publishedState(task).text }}
              </v-chip>
            </div>
            <div class="pub-row__meta">
              <span v-if="task.category?.name">{{ task.category.name }}</span>
              <span>{{ deadlineText(task.deadline) }}</span>
              <span>{{ task.participantCount }} 人领取</span>
              <span>提交 {{ task.submittedParticipantCount }} · 通过 {{ task.successfulParticipantCount }}</span>
            </div>
          </div>
          <div class="pub-row__actions">
            <v-btn
              size="small"
              variant="tonal"
              prepend-icon="mdi-chart-line"
              :to="insightsTo(task.taskId)"
              :disabled="task.visibilityStatus === 'PENDING_APPROVAL' || task.visibilityStatus === 'REJECTED'"
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
        text="这个空间里任何人都能出题 —— 想到一道就发，审过就能被人领。"
      >
        <template #actions>
          <v-btn color="primary" variant="flat" :to="publishTo()">出第一道题</v-btn>
        </template>
      </v-empty-state>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="publishedTasks.length" @update:page="page = $event" />
    </div>

    <!-- 我领取的 -->
    <div v-else class="mine__pane">
      <PanelCard v-if="participations.length" title="我领取的题">
        <ul class="claim-list">
          <li v-for="p in pagedClaimed" :key="p.participationId">
            <router-link :to="detailTo(p.taskId)" class="claim-list__title">{{ p.taskName }}</router-link>
            <span class="claim-list__by">{{ p.publisher?.name }}</span>
            <v-spacer />
            <span v-if="p.teamName" class="claim-list__team">{{ p.teamName }}</span>
            <v-chip size="x-small" label variant="tonal" :class="completionClass(p.completionStatus)">
              {{ COMPLETION_LABEL[p.completionStatus] }}
            </v-chip>
            <span class="claim-list__due">{{ deadlineText(p.deadline) }}</span>
          </li>
        </ul>
      </PanelCard>
      <v-empty-state v-else icon="mdi-hand-extended-outline" title="你还没领过题" text="去空间首页看看有没有想做的。">
        <template #actions>
          <v-btn color="primary" variant="flat" :to="homeTo()">去空间首页</v-btn>
        </template>
      </v-empty-state>

      <PageBar :page="page" :page-size="PAGE_SIZE" :total="participations.length" @update:page="page = $event" />
    </div>

    <p v-if="!isManager" class="mine__foot">
      这是普通成员视角的「我的」。所有者和管理员还会多一个「数据看板」入口，看的是整个空间而不只是自己出的题。
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

.tone-muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
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
