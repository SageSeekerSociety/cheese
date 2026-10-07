<script setup lang="ts">
/**
 * 后台「运行记录」这一屏的画面：只收结果，不取数。路由页 `AdminRunRecordsPage.vue` 取数。
 *
 * 一种事一行（服务端已经合并好）：报错在前，平台自己处理掉的在后。点开一行，右边是那
 * 种事最近一次的全文和它出现在哪些项目、对话里。
 */
import type {
  RunRecordDetail,
  RunRecordGroup,
  RunRecordGroupFilter,
  RunRecordOverview,
  RunRecordWindow,
} from '@/views/admin/runRecordsApi'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

defineOptions({ name: 'AdminRunRecordsPageView' })

const props = withDefaults(
  defineProps<{
    overview?: RunRecordOverview | null
    loading?: boolean
    failed?: boolean
    window?: RunRecordWindow
    group?: RunRecordGroupFilter
    query?: string
    /** 点开的那一种：它的 key + kind。 */
    selected?: string | null
    detail?: RunRecordDetail | null
    detailFailed?: boolean
  }>(),
  {
    overview: null,
    loading: false,
    failed: false,
    window: '24h',
    group: 'all',
    query: '',
    selected: null,
    detail: null,
    detailFailed: false,
  }
)

const emit = defineEmits<{
  'update:window': [RunRecordWindow]
  'update:group': [RunRecordGroupFilter]
  'update:query': [string]
  select: [RunRecordGroup | null]
  retry: []
}>()

const { t, n } = useI18n()

const WINDOWS: RunRecordWindow[] = ['24h', '7d', '30d']
const GROUPS: RunRecordGroupFilter[] = ['all', 'errors', 'recovered']
// 键名写全，不拼。
const WINDOW_LABEL: Record<RunRecordWindow, string> = {
  '24h': 'admin.runRecords.window.day',
  '7d': 'admin.runRecords.window.week',
  '30d': 'admin.runRecords.window.month',
}
const GROUP_LABEL: Record<RunRecordGroupFilter, string> = {
  all: 'admin.runRecords.group.all',
  errors: 'admin.runRecords.group.errors',
  recovered: 'admin.runRecords.group.recovered',
}

/** 对话里没跑完的那几轮。 */
const TURN_FAILURES = new Set(['turn_failed', 'platform_error', 'turn_timeout'])

function kindLabel(kind: string): string {
  if (kind === 'backend_error') return t('admin.runRecords.kind.backend')
  if (kind === 'frontend_error') return t('admin.runRecords.kind.frontend')
  if (TURN_FAILURES.has(kind)) return t('admin.runRecords.kind.turn')
  return t('admin.runRecords.kind.recovered')
}

function isError(g: RunRecordGroup): boolean {
  return g.kind === 'backend_error' || g.kind === 'frontend_error' || TURN_FAILURES.has(g.kind)
}

const groups = computed(() => props.overview?.groups ?? [])
const totals = computed(() => props.overview?.totals ?? null)

function idOf(g: RunRecordGroup): string {
  return `${g.kind}:${g.key}`
}

function clock(iso: string): string {
  const at = new Date(iso)
  const sameDay = new Date().toDateString() === at.toDateString()
  return sameDay
    ? at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : at.toLocaleDateString([], { month: '2-digit', day: '2-digit' })
}

function peak(g: RunRecordGroup): number {
  return Math.max(1, ...g.buckets)
}

const stack = computed(() => {
  const s = props.detail?.meta?.stack
  return typeof s === 'string' ? s : ''
})
const where = computed(() => {
  const s = props.detail?.meta?.where ?? props.detail?.meta?.page
  return typeof s === 'string' ? s : ''
})
const requestId = computed(() => {
  const s = props.detail?.meta?.request_id
  return typeof s === 'string' ? s : ''
})
const chosen = computed(() => groups.value.find((g) => idOf(g) === props.selected) ?? null)
</script>

<template>
  <AdminPage :title="t('navigation.admin.runRecords')" :sub="t('admin.runRecords.sub')">
    <template #extra>
      <div class="rr-kpis">
        <AdminKpiCard
          :label="t('admin.runRecords.kpi.errors')"
          :value="totals ? n(totals.error_kinds) : ''"
          :unit="t('admin.runRecords.kpi.kinds')"
          :loading="loading && !overview"
        />
        <AdminKpiCard
          :label="t('admin.runRecords.kpi.errorCount')"
          :value="totals ? n(totals.errors) : ''"
          :loading="loading && !overview"
        />
        <AdminKpiCard
          :label="t('admin.runRecords.kpi.recovered')"
          :value="totals ? n(totals.recovered) : ''"
          :loading="loading && !overview"
        />
      </div>
      <div class="rr-filters">
        <div class="rr-chips" role="group" :aria-label="t('admin.runRecords.windowLabel')">
          <button
            v-for="w in WINDOWS"
            :key="w"
            type="button"
            class="rr-chip"
            :class="{ 'rr-chip--on': w === window }"
            :aria-pressed="w === window"
            @click="emit('update:window', w)"
          >
            {{ t(WINDOW_LABEL[w]) }}
          </button>
        </div>
        <div class="rr-chips" role="group" :aria-label="t('admin.runRecords.groupLabel')">
          <button
            v-for="g in GROUPS"
            :key="g"
            type="button"
            class="rr-chip"
            :class="{ 'rr-chip--on': g === group }"
            :aria-pressed="g === group"
            @click="emit('update:group', g)"
          >
            {{ t(GROUP_LABEL[g]) }}
          </button>
        </div>
        <v-text-field
          class="rr-search"
          :model-value="query"
          :label="t('admin.runRecords.search')"
          prepend-inner-icon="mdi-magnify"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          @update:model-value="(v: string | null) => emit('update:query', v ?? '')"
        />
      </div>
    </template>

    <BaseLoadError v-if="failed && !overview" @retry="emit('retry')" />
    <BaseEmptyState v-else-if="overview && !groups.length" :title="t('admin.runRecords.empty')" />
    <div v-else class="rr-body" :class="{ 'rr-body--open': !!selected }">
      <div class="rr-table" role="table" :aria-label="t('navigation.admin.runRecords')">
        <div class="rr-row rr-row--head" role="row">
          <span role="columnheader" />
          <span role="columnheader">{{ t('admin.runRecords.col.kind') }}</span>
          <span role="columnheader">{{ t('admin.runRecords.col.what') }}</span>
          <span role="columnheader" class="rr-num">{{ t('admin.runRecords.col.projects') }}</span>
          <span role="columnheader" class="rr-num">{{ t('admin.runRecords.col.count') }}</span>
          <span role="columnheader">{{ t('admin.runRecords.col.span') }}</span>
          <span role="columnheader">{{ t('admin.runRecords.col.trend') }}</span>
        </div>
        <button
          v-for="g in groups"
          :key="idOf(g)"
          type="button"
          role="row"
          class="rr-row"
          :class="{ 'rr-row--on': idOf(g) === selected }"
          @click="emit('select', idOf(g) === selected ? null : g)"
        >
          <i class="rr-dot" :class="isError(g) ? 'rr-dot--error' : g.severity === 'warn' ? 'rr-dot--warn' : ''" />
          <span class="rr-kind">{{ kindLabel(g.kind) }}</span>
          <span class="rr-what" :title="g.title">{{ g.title }}</span>
          <span class="rr-num">{{ n(g.projects) }}</span>
          <span class="rr-num rr-count">{{ n(g.count) }}</span>
          <span class="rr-span">{{ clock(g.first_at) }} → {{ clock(g.last_at) }}</span>
          <span class="rr-bars" aria-hidden="true">
            <i
              v-for="(v, i) in g.buckets"
              :key="i"
              class="rr-bar"
              :class="{ 'rr-bar--error': isError(g) }"
              :style="{ height: `${Math.max(v ? 2 : 1, Math.round((16 * v) / peak(g)))}px` }"
            />
          </span>
        </button>
      </div>

      <aside v-if="selected" class="rr-detail" :aria-label="t('admin.runRecords.detail')">
        <div class="rr-detail__head">
          <i class="rr-dot" :class="chosen && isError(chosen) ? 'rr-dot--error' : ''" />
          <span class="t-title rr-detail__title">{{ chosen ? kindLabel(chosen.kind) : '' }}</span>
          <button
            type="button"
            class="rr-close"
            :aria-label="t('admin.runRecords.close')"
            @click="emit('select', null)"
          >
            <v-icon size="18">mdi-close</v-icon>
          </button>
        </div>
        <BaseLoadError v-if="detailFailed" @retry="emit('retry')" />
        <template v-else-if="detail">
          <p class="rr-detail__line">{{ detail.content }}</p>
          <dl class="rr-fields">
            <template v-if="where">
              <dt>{{ t('admin.runRecords.field.where') }}</dt>
              <dd class="rr-mono">{{ where }}</dd>
            </template>
            <template v-if="chosen">
              <dt>{{ t('admin.runRecords.field.count') }}</dt>
              <dd>{{ t('admin.runRecords.field.countValue', { count: chosen.count, projects: chosen.projects }) }}</dd>
            </template>
            <template v-if="requestId">
              <dt>{{ t('admin.runRecords.field.request') }}</dt>
              <dd class="rr-mono">{{ requestId }}</dd>
            </template>
          </dl>
          <section v-if="stack" class="rr-section">
            <h3 class="t-eyebrow">{{ t('admin.runRecords.field.stack') }}</h3>
            <pre class="rr-stack">{{ stack }}</pre>
          </section>
          <section v-if="detail.places.length" class="rr-section">
            <h3 class="t-eyebrow">{{ t('admin.runRecords.field.places') }}</h3>
            <ul class="rr-places">
              <li v-for="p in detail.places" :key="`${p.project_id}:${p.conversation_id}`">
                <a
                  v-if="p.project_id && p.conversation_id"
                  class="rr-place"
                  :href="`/projects/${p.project_id}/topics/${p.conversation_id}`"
                >
                  <span>{{ p.project ?? t('admin.runRecords.noProject') }}</span>
                  <span class="rr-mono rr-place__at">{{ clock(p.at) }}</span>
                </a>
                <span v-else class="rr-place">
                  <span>{{ p.project ?? t('admin.runRecords.noProject') }}</span>
                  <span class="rr-mono rr-place__at">{{ clock(p.at) }}</span>
                </span>
              </li>
            </ul>
            <p v-if="detail.more_places" class="t-meta">
              {{ t('admin.runRecords.morePlaces', { count: detail.more_places }) }}
            </p>
          </section>
        </template>
      </aside>
    </div>
  </AdminPage>
</template>

<style scoped>
.rr-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}
.rr-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  margin-top: 16px;
}
.rr-chips {
  display: flex;
  gap: 8px;
}
.rr-chip {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  background: var(--surface);
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rr-chip:hover {
  background: var(--fill);
}
.rr-chip--on {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--surface);
}
.rr-chip--on:hover {
  background: var(--ink);
}
.rr-search {
  flex: 1 1 200px;
  max-width: 280px;
  margin-left: auto;
}
.rr-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}
/* 点开一行之后右边多出 360 的详情栏，判的是**这一列**还剩多少地方，不是窗口有多宽：
   这一页在后台容器里（`.app-page__column--admin`，名字 `admin`），侧栏收起省出的宽度
   视口查询看不见（§3.5、§10.9）。1200 那条视口线挪到容器上取 900——后台容器已有的值
   （`AdminModelsAudit`）。换轴会挪动开并排的位置，真浏览器里逐档量过：侧栏展开时这一列
   只有 845（视口 1200）、收起时 925（视口 1000），旧的 1200 恰好在列最窄处开、在列更宽
   的 1000–1179 反而没开；现在列 ≥ 900 就开。列 900–996 那一段并排时表格横向滚
   （`.rr-table` 是 `overflow-x: auto`，`min-width: 620` 的行放进 549 的表列），
   旧规则在 1280（列 925）下本来就是这个样子。 */
@container admin (min-width: 900px) {
  .rr-body--open {
    grid-template-columns: minmax(0, 1fr) 360px;
  }
}
.rr-table {
  overflow-x: auto;
}
.rr-row {
  display: grid;
  grid-template-columns: 10px 80px minmax(160px, 1fr) 48px 56px 104px 76px;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 620px;
  padding: 8px 12px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: none;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rr-row:hover {
  background: var(--fill);
}
.rr-row--on {
  background: var(--accent-wash);
}
.rr-row--head {
  font-size: 12px;
  color: var(--faint);
  cursor: default;
}
.rr-row--head:hover {
  background: none;
}
.rr-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
.rr-dot--error {
  background: var(--danger);
}
.rr-dot--warn {
  background: var(--warn);
}
.rr-kind {
  color: var(--muted);
}
.rr-what {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rr-num {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.rr-count {
  color: var(--ink);
}
.rr-span {
  font-size: 12px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.rr-bars {
  display: flex;
  align-items: flex-end;
  gap: 1px;
  height: 16px;
}
.rr-bar {
  width: 2px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
.rr-bar--error {
  background: var(--danger);
}
.rr-detail {
  align-self: start;
  padding: 12px 16px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.rr-detail__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rr-detail__title {
  flex: 1 1 auto;
}
.rr-close {
  display: inline-flex;
  padding: 4px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.rr-close:hover {
  background: var(--fill);
}
.rr-detail__line {
  margin: 12px 0 0;
  font-size: 14px;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.rr-fields {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 4px 12px;
  margin: 12px 0 0;
  font-size: 13px;
}
.rr-fields dt {
  color: var(--faint);
}
.rr-fields dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.rr-mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.rr-section {
  margin-top: 16px;
}
.rr-stack {
  max-height: 240px;
  margin: 8px 0 0;
  padding: 8px 12px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--canvas);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.rr-places {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}
.rr-place {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--text);
  text-decoration: none;
}
.rr-places li + li {
  margin-top: 4px;
}
a.rr-place:hover {
  background: var(--fill);
}
.rr-place__at {
  color: var(--faint);
}
</style>
