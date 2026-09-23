<script setup lang="ts">
// 整板数据看板。只对所有者与管理员开放 —— 与今天一致（后端 `/analytics/*`
// 全部挂在 `_ensure_space_admin` 后面）。
//
// 这一屏的次序是**按问题排的，不是按数据表排的**：
//
//   1. 现在怎么样    → 总览：六个 KPI + 走势 + 构成
//   2. 有什么要我做  → 预警：待审、无人领取、临近截止、领了不动
//   3. 哪道题、哪个人、哪个出题人不对劲 → 题目 / 人 / 出题人 三格
//
// 反过来的排法（先铺一堆表让人自己找）是今天 analytics 那几个 tab 的样子；
// 「重点设计」要的就是把这个次序立起来。
import { computed, ref } from 'vue'

import BarList from '../components/BarList.vue'
import MetricCard from '../components/MetricCard.vue'
import PanelCard from '../components/PanelCard.vue'
import SplitBar from '../components/SplitBar.vue'
import TrendChart from '../components/TrendChart.vue'
import { DAY_LABELS, SUMMARY, deadlineText, isOpen } from '../fixtures'
import {
  CATEGORY_SPLIT,
  boardTasks,
  claimRanking,
  kpis,
  pendingTasks,
  publisherRanking,
  statusSplit,
  tasks,
} from '../store'

const tab = ref('overview')

const trendClaim = computed(() => {
  // 累计领取：把每道题的最后一点加起来。原型里直接给一条造好的曲线，
  // 免得每个组件各算一遍、各算出一个数。
  const total = kpis.value.claims
  const shape = SUMMARY.activeTrend
  const max = Math.max(...shape)
  return shape.map((v) => Math.round((v / max) * total))
})

const stalled = computed(() =>
  tasks.value.flatMap((t) =>
    t.claims
      .filter((c) => c.status === 'IN_PROGRESS' && Date.now() - new Date(c.at).getTime() > 5 * 86_400_000)
      .map((c) => ({ task: t, who: c })),
  ),
)

const closingSoon = computed(() =>
  boardTasks.value.filter((t) => {
    const ms = new Date(t.deadline).getTime() - Date.now()
    return ms > 0 && ms < 3 * 86_400_000
  }),
)

const coldTasks = computed(() => boardTasks.value.filter((t) => t.claims.length === 0))

const alerts = computed(() => [
  {
    key: 'pending',
    icon: 'mdi-clipboard-check-outline',
    tone: 'warn' as const,
    title: `${pendingTasks.value.length} 道题在等你审`,
    detail: '审过才会上板，作者一直在等。',
    to: '/review',
    cta: '去审核',
    count: pendingTasks.value.length,
  },
  {
    key: 'closing',
    icon: 'mdi-timer-outline',
    tone: 'danger' as const,
    title: `${closingSoon.value.length} 道题三天内截止`,
    detail: closingSoon.value.map((t) => t.title).slice(0, 2).join('、') || '—',
    count: closingSoon.value.length,
  },
  {
    key: 'stalled',
    icon: 'mdi-account-clock-outline',
    tone: 'warn' as const,
    title: `${stalled.value.length} 人领了题、五天没动`,
    detail: '可能是题目太难开口，也可能是口径不清楚。',
    count: stalled.value.length,
  },
  {
    key: 'cold',
    icon: 'mdi-snowflake',
    tone: 'muted' as const,
    title: `${coldTasks.value.length} 道题上板后无人领取`,
    detail: coldTasks.value.map((t) => t.title).slice(0, 2).join('、') || '—',
    count: coldTasks.value.length,
  },
])

const alertCount = computed(() => alerts.value.filter((a) => a.count > 0).length)

// --- 各 tab 的行数据 -----------------------------------------------------------

const taskRows = computed(() =>
  [...tasks.value].sort((a, b) => b.claims.length - a.claims.length),
)

const participantRows = computed(() => {
  const map = new Map<string, { name: string; claims: number; passed: number; active: number }>()
  for (const t of tasks.value) {
    for (const c of t.claims) {
      const row = map.get(c.handle) ?? { name: c.name, claims: 0, passed: 0, active: 0 }
      row.claims += 1
      if (c.status === 'PASSED') row.passed += 1
      if (c.status === 'IN_PROGRESS' || c.status === 'SUBMITTED') row.active += 1
      map.set(c.handle, row)
    }
  }
  return [...map.values()].sort((a, b) => b.claims - a.claims)
})

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0)
</script>

<template>
  <div class="an">
    <div class="an__head">
      <div>
        <h1>数据看板</h1>
        <p>
          整块板的情况。这一屏只对所有者和管理员开放 —— 普通用户在「我的」里只看得到自己出的题。
        </p>
      </div>
      <v-chip v-if="alertCount" color="warning" variant="tonal" label>{{ alertCount }} 件待处理</v-chip>
    </div>

    <v-tabs v-model="tab" density="comfortable" class="an__tabs">
      <v-tab value="overview">总览</v-tab>
      <v-tab value="alerts">待处理{{ pendingTasks.length ? `（${pendingTasks.length}）` : '' }}</v-tab>
      <v-tab value="tasks">题目</v-tab>
      <v-tab value="people">人</v-tab>
      <v-tab value="publishers">出题人</v-tab>
    </v-tabs>

    <!-- ===== 总览：先回答「现在怎么样」 ===== -->
    <div v-if="tab === 'overview'" class="an__pane">
      <div class="an__kpis">
        <MetricCard label="题目总数" :value="kpis.taskTotal" icon="mdi-file-document-multiple-outline" :hint="`${kpis.published} 道已上板`" />
        <MetricCard
          label="待审核"
          :value="kpis.pending"
          icon="mdi-clock-outline"
          :tone="kpis.pending ? 'warn' : 'muted'"
          :hint="kpis.pending ? '审完才会出现在板上' : '队列是空的'"
        />
        <MetricCard label="参与人数" :value="kpis.participants" icon="mdi-account-group-outline" :hint="`本周新增 ${SUMMARY.newMembersThisWeek} 人`" />
        <MetricCard
          label="领取总数"
          :value="kpis.claims"
          icon="mdi-hand-extended-outline"
          :tone="kpis.claims >= SUMMARY.claimsLastWeek ? 'ok' : 'warn'"
          :hint="`上周同期 ${SUMMARY.claimsLastWeek}`"
        />
        <MetricCard label="提交总数" :value="kpis.submissions" icon="mdi-tray-arrow-up" hint="含已通过的" />
        <MetricCard
          label="完成率"
          :value="`${kpis.completionRate}%`"
          icon="mdi-progress-check"
          :tone="kpis.completionRate >= 60 ? 'ok' : 'warn'"
          hint="通过 / 提交"
        />
      </div>

      <div class="an__grid">
        <PanelCard class="an__span2" title="领取与提交" subtitle="最近 12 天">
          <TrendChart
            :labels="DAY_LABELS"
            :series="[
              { name: '累计领取', values: trendClaim },
              { name: '累计提交', values: SUMMARY.submitTrend.map((v) => Math.round((v / Math.max(...SUMMARY.submitTrend)) * kpis.submissions)) },
            ]"
            :height="220"
          />
        </PanelCard>

        <PanelCard title="题目构成" subtitle="按状态">
          <SplitBar :segments="statusSplit" />
        </PanelCard>

        <PanelCard title="分类分布" subtitle="题目数">
          <BarList :rows="CATEGORY_SPLIT.map((c) => ({ label: c.label, value: c.count }))" unit=" 道" />
        </PanelCard>

        <PanelCard title="最热的题" subtitle="按领取人数">
          <BarList :rows="claimRanking.map((t) => ({ label: t.title, value: t.claims.length }))" unit=" 人" />
        </PanelCard>

        <PanelCard title="出题最多的" subtitle="按累计被领取">
          <BarList
            :rows="publisherRanking.map((p) => ({ label: p.person.name, value: p.claims, hint: `${p.tasks} 道题` }))"
            unit=" 次"
          />
        </PanelCard>
      </div>
    </div>

    <!-- ===== 待处理：再回答「要我做点什么」 ===== -->
    <div v-else-if="tab === 'alerts'" class="an__pane">
      <div class="an__alerts">
        <div v-for="a in alerts" :key="a.key" class="alert" :class="[`alert--${a.tone}`, { 'alert--zero': a.count === 0 }]">
          <v-icon :icon="a.icon" size="20" />
          <div class="alert__body">
            <b>{{ a.title }}</b>
            <span>{{ a.detail }}</span>
          </div>
          <v-btn v-if="a.to" size="small" variant="tonal" :to="a.to">{{ a.cta }}</v-btn>
        </div>
      </div>

      <PanelCard v-if="stalled.length" title="领了但没动的" subtitle="超过五天还是「进行中」">
        <ul class="mini">
          <li v-for="row in stalled" :key="`${row.task.id}-${row.who.handle}`">
            <span class="mini__who">{{ row.who.name }}</span>
            <router-link :to="`/task/${row.task.id}`" class="mini__title">{{ row.task.title }}</router-link>
            <v-spacer />
            <span class="mini__meta">{{ row.task.publisher.name }} 出题</span>
          </li>
        </ul>
      </PanelCard>

      <PanelCard v-if="coldTasks.length" title="上板后没人领的" subtitle="发出去两周还是零领取，值得回看题目本身">
        <ul class="mini">
          <li v-for="t in coldTasks" :key="t.id">
            <router-link :to="`/task/${t.id}`" class="mini__title">{{ t.title }}</router-link>
            <v-spacer />
            <span class="mini__meta">{{ t.publisher.name }} · {{ deadlineText(t) }}</span>
          </li>
        </ul>
      </PanelCard>
    </div>

    <!-- ===== 题目 ===== -->
    <div v-else-if="tab === 'tasks'" class="an__pane">
      <PanelCard title="全部题目" :subtitle="`${taskRows.length} 道 · 按领取人数排`">
        <v-table density="comfortable" class="an__table">
          <thead>
            <tr>
              <th>题目</th>
              <th>出题人</th>
              <th class="num">领取</th>
              <th class="num">提交</th>
              <th class="num">通过率</th>
              <th>状态</th>
              <th>截止</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in taskRows" :key="t.id">
              <td>
                <router-link :to="`/task/${t.id}`" class="an__link">{{ t.title }}</router-link>
                <div class="an__sub">{{ t.category }}</div>
              </td>
              <td>{{ t.publisher.name }}</td>
              <td class="num">{{ t.claims.length }}<span v-if="t.participantLimit !== null" class="an__cap"> / {{ t.participantLimit }}</span></td>
              <td class="num">{{ t.submitted }}</td>
              <td class="num">{{ t.submitted ? pct(t.passed, t.submitted) : 0 }}%</td>
              <td>
                <v-chip size="x-small" label variant="tonal" :class="t.state === 'PUBLISHED' ? 'tone-ok' : t.state === 'PENDING' ? 'tone-warn' : 'tone-danger'">
                  {{ t.state === 'PUBLISHED' ? (isOpen(t) ? '已上板' : '已截止') : t.state === 'PENDING' ? '待审核' : '已驳回' }}
                </v-chip>
              </td>
              <td class="an__sub">{{ deadlineText(t) }}</td>
            </tr>
          </tbody>
        </v-table>
      </PanelCard>
    </div>

    <!-- ===== 人 ===== -->
    <div v-else-if="tab === 'people'" class="an__pane">
      <PanelCard title="参与者" :subtitle="`${participantRows.length} 人领过题 · 按领取数排`">
        <v-table density="comfortable" class="an__table">
          <thead>
            <tr>
              <th>成员</th>
              <th class="num">领取</th>
              <th class="num">通过</th>
              <th class="num">在做的</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in participantRows" :key="p.name">
              <td>{{ p.name }}</td>
              <td class="num">{{ p.claims }}</td>
              <td class="num">{{ p.passed }}</td>
              <td class="num">{{ p.active }}</td>
              <td>
                <v-chip size="x-small" label variant="tonal" :class="p.active ? 'tone-warn' : 'tone-ok'">
                  {{ p.active ? `${p.active} 道在做` : '都收尾了' }}
                </v-chip>
              </td>
            </tr>
          </tbody>
        </v-table>
      </PanelCard>
    </div>

    <!-- ===== 出题人 ===== -->
    <div v-else class="an__pane">
      <PanelCard title="出题人" subtitle="开放发题之后，这一格是看「谁在认真出题」的地方">
        <v-table density="comfortable" class="an__table">
          <thead>
            <tr>
              <th>出题人</th>
              <th class="num">题目数</th>
              <th class="num">累计被领取</th>
              <th class="num">平均每道被领</th>
              <th class="num">出题通过率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in publisherRanking" :key="p.person.handle">
              <td>{{ p.person.name }}</td>
              <td class="num">{{ p.tasks }}</td>
              <td class="num">{{ p.claims }}</td>
              <td class="num">{{ p.tasks ? Math.round(p.claims / p.tasks) : 0 }}</td>
              <td class="num">{{ p.submitted ? pct(p.passed, p.submitted) : 0 }}%</td>
            </tr>
          </tbody>
        </v-table>
        <p class="an__note">
          「出题通过率」是这位出题人出的题里，领取者最终通过的比例 —— 高不一定是好事（可能题太水），
          低也不一定是坏事（可能题够硬）。列在这里是给人**对照**用的，不是打分。
        </p>
      </PanelCard>
    </div>

    <p class="an__foot">
      普通用户在「我的」里能看到的是自己的那几道题（领取走势、领取者名单、完成情况），看不到这一屏的任何汇总。
    </p>
  </div>
</template>

<style scoped lang="scss">
.an__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.an__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.an__head p {
  max-width: 660px;
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.83rem;
  line-height: 1.7;
}

.an__tabs {
  margin-bottom: 18px;
}

.an__pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.an__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  gap: 12px;
}

.an__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

@media (max-width: 900px) {
  .an__grid {
    grid-template-columns: 1fr;
  }
}

.an__span2 {
  grid-column: span 2;
}

@media (max-width: 900px) {
  .an__span2 {
    grid-column: span 1;
  }
}

.an__alerts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.alert {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px;
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 12px;
}

.alert--zero {
  opacity: 0.5;
}

.alert--warn {
  color: rgb(var(--v-theme-warning));
}

.alert--danger {
  color: rgb(var(--v-theme-error));
}

.alert--muted {
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.alert__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  color: rgb(var(--v-theme-on-surface));
}

.alert__body b {
  font-size: 0.86rem;
}

.alert__body span {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.75rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini {
  padding: 0;
  margin: 0;
  list-style: none;
}

.mini li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 0;
  font-size: 0.83rem;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.mini li:first-child {
  border-top: none;
}

.mini__who {
  font-weight: 600;
}

.mini__title {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.8);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mini__meta {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
}

.an__table {
  background: transparent;
}

.an__table :deep(th) {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.75rem;
  font-weight: 500;
}

.an__table :deep(td) {
  font-size: 0.83rem;
}

.an__table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.an__link {
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.an__link:hover {
  text-decoration: underline;
}

.an__sub {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
}

.an__cap {
  color: rgba(var(--v-theme-on-surface), 0.4);
}

.an__note {
  margin: 14px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.78rem;
  line-height: 1.7;
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

.an__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
}
</style>
