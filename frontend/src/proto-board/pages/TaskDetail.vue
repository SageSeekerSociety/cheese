<script setup lang="ts">
// 题目详情。领取按钮**有状态**：没领 / 进行中 / 已提交 / 已通过，四种状态说四种话。
//
// 右侧那一栏是「这道题现在什么情况」，但只对出题人和管理员展开明细；普通领取者
// 只看得到自己的状态和别人领取的进度条 —— 谁领了是板内公开信息，谁做到哪一步不是。
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PanelCard from '../components/PanelCard.vue'
import TrendChart from '../components/TrendChart.vue'
import { CLAIM_LABEL, DAY_LABELS, deadlineText, isOpen, STATE_LABEL } from '../fixtures'
import { alreadyClaimed, canManageTask, claimTask, myClaim, tasks } from '../store'

const route = useRoute()
const router = useRouter()

const task = computed(() => tasks.value.find((t) => t.id === String(route.params.id)))

const claimed = computed(() => (task.value ? alreadyClaimed(task.value) : false))
const mine = computed(() => (task.value ? myClaim(task.value) : undefined))
const full = computed(
  () => task.value?.participantLimit !== null && (task.value?.claims.length ?? 0) >= (task.value?.participantLimit ?? 0)
)
const open = computed(() => (task.value ? isOpen(task.value) : false))
const canSeeRoster = computed(() => (task.value ? canManageTask(task.value) : false))

const claimBlocked = ref(false)

const fillPct = computed(() => {
  const t = task.value
  if (!t || t.participantLimit === null) return 0
  return Math.min(100, Math.round((t.claims.length / t.participantLimit) * 100))
})

function doClaim() {
  if (!task.value) return
  if (!claimTask(task.value.id)) claimBlocked.value = true
}

const claimLabel = computed(() => {
  const t = task.value
  if (!t) return ''
  if (t.state === 'PENDING') return '待审核'
  if (t.state === 'REJECTED') return '已驳回'
  if (!open.value) return '已截止'
  if (claimed.value) return '你已经领取'
  if (full.value) return '人数已满'
  return '领取这道题'
})
</script>

<template>
  <div v-if="task" class="td">
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" class="td__back" @click="router.back()"
      >返回</v-btn
    >

    <div class="td__grid">
      <div class="td__main">
        <PanelCard>
          <div class="td__top">
            <v-chip size="x-small" label variant="tonal" :class="`tone-${task.state.toLowerCase()}`">
              {{ task.state === 'PUBLISHED' && !open ? '已截止' : STATE_LABEL[task.state] }}
            </v-chip>
            <v-chip size="x-small" label variant="text">{{ task.category }}</v-chip>
            <v-spacer />
            <span class="td__by">{{ task.publisher.name }} 出题</span>
          </div>
          <h1 class="td__title">{{ task.title }}</h1>
          <p class="td__summary">{{ task.summary }}</p>
          <div class="td__tags">
            <v-chip v-for="tag in task.tags" :key="tag" size="x-small" label variant="text">#{{ tag }}</v-chip>
          </div>

          <div v-if="task.state === 'REJECTED' && task.rejectReason" class="td__reject">
            <v-icon icon="mdi-alert-circle-outline" size="18" />
            <div>
              <b>被驳回：{{ task.rejectReason }}</b>
              <span v-if="canManageTask(task)" class="td__reject-hint">改完可以重新提交。</span>
            </div>
          </div>

          <div class="td__claim">
            <v-btn
              color="primary"
              variant="flat"
              size="large"
              :disabled="task.state !== 'PUBLISHED' || !open || claimed || full"
              :prepend-icon="claimed ? 'mdi-check' : 'mdi-hand-extended-outline'"
              @click="doClaim"
            >
              {{ claimLabel }}
            </v-btn>
            <div v-if="mine" class="td__mine-state">
              你的状态：<b>{{ CLAIM_LABEL[mine.status] }}</b>
              <span v-if="mine.team"> · {{ mine.team }}</span>
            </div>
            <div v-else-if="full" class="td__mine-state td__mine-state--warn">
              领取人数已经到上限（{{ task.claims.length }} / {{ task.participantLimit }}），这道题不再接受新的领取。
            </div>
            <div v-else class="td__mine-state">{{ deadlineText(task) }} · 领取后你会出现在出题人的看板上。</div>
          </div>
        </PanelCard>

        <!-- 出题人/管理员才看得到名单明细。普通领取者只看到进度条。 -->
        <PanelCard v-if="canSeeRoster" title="领取者" :subtitle="`${task.claims.length} 人已领`">
          <ul v-if="task.claims.length" class="roster">
            <li v-for="c in task.claims" :key="c.handle">
              <v-avatar size="24" class="roster__avatar">{{ c.name.slice(0, 1) }}</v-avatar>
              <span class="roster__name">{{ c.name }}</span>
              <span v-if="c.team" class="roster__team">{{ c.team }}</span>
              <v-spacer />
              <v-chip size="x-small" label variant="tonal" :class="`claim-${c.status.toLowerCase()}`">
                {{ CLAIM_LABEL[c.status] }}
              </v-chip>
            </li>
          </ul>
          <v-empty-state v-else icon="mdi-account-outline" title="还没有人领取" />
        </PanelCard>
      </div>

      <aside class="td__side">
        <PanelCard title="领取进度">
          <div class="pd">
            <div class="pd__num">
              <b>{{ task.claims.length }}</b>
              <span v-if="task.participantLimit !== null"> / {{ task.participantLimit }}</span>
              <em v-else> 人（不限）</em>
            </div>
            <v-progress-linear
              v-if="task.participantLimit !== null"
              :model-value="fillPct"
              height="6"
              rounded
              class="pd__bar"
            />
          </div>
          <dl class="facts">
            <div>
              <dt>小队</dt>
              <dd>
                {{
                  task.minTeamSize === 1 && task.maxTeamSize === 1
                    ? '单人'
                    : `${task.minTeamSize}–${task.maxTeamSize} 人`
                }}
              </dd>
            </div>
            <div>
              <dt>截止</dt>
              <dd>{{ deadlineText(task) }}</dd>
            </div>
            <div>
              <dt>提交</dt>
              <dd>{{ task.submitted }} 份</dd>
            </div>
            <div>
              <dt>通过</dt>
              <dd>{{ task.passed }} 份</dd>
            </div>
          </dl>
        </PanelCard>

        <PanelCard v-if="task.claimTrend.length" title="领取走势" subtitle="最近 12 天">
          <TrendChart :labels="DAY_LABELS" :series="[{ name: '累计领取', values: task.claimTrend }]" :height="150" />
        </PanelCard>

        <PanelCard v-if="canManageTask(task)" title="出题人视角">
          <p class="side-note">这道题是你（或你可管理）的，所以你能看到上面那份领取者名单和这张走势图。</p>
          <v-btn block variant="tonal" size="small" :to="`/insights/${task.id}`" prepend-icon="mdi-chart-line">
            打开这道题的看板
          </v-btn>
        </PanelCard>
        <PanelCard v-else title="你看不到什么" dense>
          <p class="side-note">
            领取者名单、每道题的走势与完成情况，只对<b>出题人本人和管理员</b>开放。普通用户看得到人数，看不到是谁做到哪一步。
          </p>
        </PanelCard>
      </aside>
    </div>
  </div>

  <v-empty-state v-else icon="mdi-help-circle-outline" title="找不到这道题" text="它可能被删了，或者你打错了地址。" />
</template>

<style scoped lang="scss">
.td__back {
  margin-bottom: 10px;
}

.td__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .td__grid {
    grid-template-columns: 1fr;
  }
}

.td__main,
.td__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.td__top {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.tone-published {
  color: rgb(var(--v-theme-success));
}

.tone-pending {
  color: rgb(var(--v-theme-warning));
}

.tone-rejected {
  color: rgb(var(--v-theme-error));
}

.td__by {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
}

.td__title {
  margin: 0 0 10px;
  font-size: 1.25rem;
  font-weight: 650;
  line-height: 1.4;
}

.td__summary {
  margin: 0 0 12px;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.9rem;
  line-height: 1.8;
  white-space: pre-wrap;
}

.td__tags {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
}

.td__reject {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  margin-bottom: 16px;
  color: rgb(var(--v-theme-error));
  font-size: 0.83rem;
  line-height: 1.6;
  background: rgba(var(--v-theme-error), 0.07);
  border-radius: var(--radius-md);
}

.td__reject-hint {
  display: block;
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.td__claim {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
  padding-top: 16px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.td__mine-state {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.79rem;
}

.td__mine-state--warn {
  color: rgb(var(--v-theme-warning));
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
  padding: 8px 0;
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
  padding: 1px 7px;
  color: rgba(var(--v-theme-on-surface), 0.55);
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

.pd__num {
  font-size: 1.5rem;
  font-weight: 650;
}

.pd__num b {
  font-size: 2rem;
}

.pd__num span,
.pd__num em {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.9rem;
  font-style: normal;
}

.pd__bar {
  margin-top: 10px;
}

.facts {
  margin: 16px 0 0;
}

.facts > div {
  display: flex;
  justify-content: space-between;
  padding: 7px 0;
  font-size: 0.82rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.facts dt {
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.facts dd {
  margin: 0;
}

.side-note {
  margin: 0 0 12px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.79rem;
  line-height: 1.7;
}
</style>
