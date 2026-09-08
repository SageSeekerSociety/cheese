<script setup lang="ts">
import type { Contributions, InboxItem, ProjectCredits, ProjectOverview, TopicRef } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import DOMPurify from 'dompurify'

import { getContributions, getInbox, getOverview, getProject, getProjectCredits, markRead, sendFeedback } from '../api'
import { label, NOTIF_KIND, PROJECT_ROLE, TOPIC_STATUS } from '../labels'
import { myHandle } from '../me'

import { markdown } from '@/lib/markdown'

const props = defineProps<{ projectId: string }>()

const ME = myHandle()

const overview = ref<ProjectOverview | null>(null)
const inbox = ref<InboxItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// 概要. Read-only here: the overview (or the project card) carries it and
// nothing in this view writes it back. The 生成/刷新 button that used to sit in
// the section head is gone along with the POST behind it — parking a feature has
// to include its entry point, or the user reads the leftover button as "this is
// broken" rather than "this is off".
const summary = ref<string>('')

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(markdown.parse(text, { async: false }) as string)
}

// waiting_on_you is keyed by handle. Pull out my items vs. everyone else's.
const myWaiting = computed<TopicRef[]>(() => overview.value?.waiting_on_you?.[ME] ?? [])
const othersWaiting = computed<{ handle: string; items: TopicRef[] }[]>(() => {
  const map = overview.value?.waiting_on_you ?? {}
  return Object.entries(map)
    .filter(([handle, items]) => handle !== ME && items.length > 0)
    .map(([handle, items]) => ({ handle, items }))
})

const statusEntries = computed<[string, number][]>(() => Object.entries(overview.value?.topics_by_status ?? {}))

// upcoming_milestones includes next_milestone; drop it so the emphasized "next"
// row isn't repeated in the list below it.
const restMilestones = computed(() => {
  const list = overview.value?.upcoming_milestones ?? []
  const next = overview.value?.next_milestone
  if (!next) return list
  let skipped = false
  return list.filter((m) => {
    if (!skipped && m.title === next.title && m.due_date === next.due_date) {
      skipped = true
      return false
    }
    return true
  })
})

// ---- 算力额度 (spec §9.1): grant balance; unlimited when no linked task ----
const credits = ref<ProjectCredits | null>(null)
const creditsUsedPct = computed<number>(() => {
  const c = credits.value
  if (!c || c.unlimited || c.credits_total <= 0) return 0
  return Math.min(100, (c.credits_used / c.credits_total) * 100)
})
const creditsExhausted = computed<boolean>(() => {
  const c = credits.value
  return !!c && !c.unlimited && c.credits_remaining <= 0
})

function fmtCredits(n: number): string {
  // Credits are fractional (1 credit = 1万 tokens); show a sensible precision.
  if (Math.abs(n) >= 100) return n.toFixed(0)
  if (Math.abs(n) >= 1) return n.toFixed(2)
  return n.toFixed(4)
}

// ---- 贡献图 (§10.1): human vs AI split ----
const contributions = ref<Contributions | null>(null)
const humanCount = computed<number>(() => contributions.value?.by_author_type.human ?? 0)
const aiCount = computed<number>(() => contributions.value?.by_author_type.ai ?? 0)
const contribTotal = computed<number>(() => humanCount.value + aiCount.value)
const humanPct = computed<number>(() => (contribTotal.value === 0 ? 0 : (humanCount.value / contribTotal.value) * 100))
const aiPct = computed<number>(() => (contribTotal.value === 0 ? 0 : (aiCount.value / contribTotal.value) * 100))

function fmtDate(d: string | null): string {
  if (!d) return '待定'
  // Accept ISO strings; show the date part only.
  return d.length >= 10 ? d.slice(0, 10) : d
}

// Status dots: each topic status maps to a small semantic/neutral dot.
function statusDotColor(status: string): string {
  if (status === 'active') return 'var(--ok)'
  if (status === 'draft') return 'var(--faint)'
  if (status === 'archived') return 'var(--line-2)'
  return 'var(--faint)'
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [ov, ib, contrib, cred] = await Promise.all([
      getOverview(props.projectId),
      // A 401/403 here (signed out, or a stale cached handle after switching
      // accounts) must not blank the whole overview — degrade to an empty inbox.
      getInbox(props.projectId, ME).catch(() => null),
      getContributions(props.projectId).catch(() => null),
      getProjectCredits(props.projectId).catch(() => null),
    ])
    overview.value = ov
    inbox.value = ib?.data ?? []
    contributions.value = contrib
    credits.value = cred
    // The overview extends the project card; if it didn't carry summary, fetch
    // the project to get it.
    if (typeof ov.summary === 'string') {
      summary.value = ov.summary
    } else {
      try {
        const project = await getProject(props.projectId)
        summary.value = project.summary ?? ''
      } catch {
        summary.value = ''
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载总览失败'
  } finally {
    loading.value = false
  }
}

async function onMarkRead(item: InboxItem) {
  try {
    await markRead(item.id)
    item.read = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : '标记已读失败'
  }
}

async function onFeedback(item: InboxItem, feedback: 'up' | 'down') {
  try {
    await sendFeedback(item.id, feedback)
    item.feedback = feedback
  } catch (e) {
    error.value = e instanceof Error ? e.message : '反馈失败'
  }
}

watch(() => props.projectId, load)
onMounted(load)
</script>

<template>
  <div class="overview-page fill-height overflow-y-auto">
    <v-container class="py-6 page-container page-container--wide">
      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable">
        {{ error }}
      </v-alert>

      <template v-else-if="overview">
        <div class="mb-6">
          <div class="t-eyebrow mb-1">项目总览</div>
          <h1 class="t-page-title">{{ overview.name }}</h1>
        </div>

        <!-- 概要 — rendered only when one exists. Generation is parked, so
             an empty box with a dead button would read as a broken feature. -->
        <section v-if="summary" class="page-section">
          <div class="page-section-head">
            <span class="page-section-title">概要</span>
          </div>
          <div class="page-section-body">
            <div class="md-content" v-html="renderMarkdown(summary)" />
          </div>
        </section>

        <!-- 等你处理的事 (subtle accent, not a hero block) -->
        <section class="page-section">
          <div class="page-section-head">
            <span class="ln-accent-dot" />
            <span class="page-section-title">等你处理的事</span>
            <span v-if="myWaiting.length" class="ln-count">{{ myWaiting.length }}</span>
          </div>
          <div class="page-section-body">
            <div v-if="myWaiting.length === 0" class="c-faint t-body py-2">暂无待处理的事</div>
            <div v-else>
              <div v-for="t in myWaiting" :key="t.id" class="ln-row">
                <span class="ln-dot ln-dot-warn" />
                <span class="ln-row-title">{{ t.title }}</span>
                <v-spacer />
                <span class="ln-tag">{{ label(NOTIF_KIND, t.kind) }}</span>
              </div>
            </div>

            <template v-if="othersWaiting.length">
              <div class="ln-subhead">其他成员待办</div>
              <div v-for="g in othersWaiting" :key="g.handle" class="mb-1">
                <div class="text-caption font-weight-medium text-medium-emphasis ln-group-label">@{{ g.handle }}</div>
                <div v-for="t in g.items" :key="t.id" class="ln-row">
                  <span class="ln-dot ln-dot-muted" />
                  <span class="ln-row-title">{{ t.title }}</span>
                  <v-spacer />
                  <span class="ln-tag">{{ label(NOTIF_KIND, t.kind) }}</span>
                </div>
              </div>
            </template>
          </div>
        </section>

        <v-row>
          <!-- 里程碑 -->
          <v-col cols="12" md="6">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">里程碑</span>
              </div>
              <div class="page-section-body">
                <div v-if="overview.next_milestone" class="ln-row">
                  <span class="ln-dot ln-dot-ink" />
                  <span class="ln-row-title" style="font-weight: 500; color: var(--ink)">
                    {{ overview.next_milestone.title }}
                  </span>
                  <v-spacer />
                  <span class="ln-num text-medium-emphasis">
                    {{ fmtDate(overview.next_milestone.due_date) }}
                  </span>
                </div>
                <div v-else class="text-medium-emphasis text-body-2 py-2">暂无里程碑</div>

                <div v-for="(m, i) in restMilestones" :key="i" class="ln-row">
                  <span class="ln-dot ln-dot-muted" />
                  <span class="ln-row-title">{{ m.title }}</span>
                  <v-spacer />
                  <span class="ln-num text-medium-emphasis">{{ fmtDate(m.due_date) }}</span>
                </div>
              </div>
            </section>
          </v-col>

          <!-- 话题 × 状态 (dot + label, aligned counts) -->
          <v-col cols="12" md="6">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">话题</span>
                <v-spacer />
                <span class="ln-num text-medium-emphasis">共 {{ overview.topic_count }}</span>
              </div>
              <div class="page-section-body">
                <div v-if="statusEntries.length === 0" class="text-medium-emphasis text-body-2 py-2">暂无话题</div>
                <div v-else>
                  <div v-for="[s, n] in statusEntries" :key="s" class="ln-row">
                    <span class="ln-dot" :style="{ background: statusDotColor(s) }" />
                    <span class="ln-row-title">{{ label(TOPIC_STATUS, s) }}</span>
                    <v-spacer />
                    <span class="ln-num">{{ n }}</span>
                  </div>
                </div>
              </div>
            </section>
          </v-col>

          <!-- 贡献 · 人 / AI (§10.1) -->
          <v-col cols="12" md="6">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">工作记录 · 人 / AI</span>
              </div>
              <div class="page-section-body">
                <div v-if="contribTotal === 0" class="text-medium-emphasis text-body-2 py-2">暂无工作记录</div>
                <template v-else>
                  <p class="text-medium-emphasis text-body-2 mb-3">按消息和文档内容块计数，不代表工作量或贡献评分</p>
                  <div class="contrib-bar mb-3">
                    <div class="contrib-seg contrib-human" :style="{ width: humanPct + '%' }" />
                    <div class="contrib-seg contrib-ai" :style="{ width: aiPct + '%' }" />
                  </div>
                  <div class="ln-row">
                    <span class="ln-dot dot-human" />
                    <span class="ln-row-title">人</span>
                    <v-spacer />
                    <span class="ln-num">{{ humanCount }}</span>
                  </div>
                  <div class="ln-row">
                    <span class="ln-dot dot-ai" />
                    <span class="ln-row-title">AI</span>
                    <v-spacer />
                    <span class="ln-num">{{ aiCount }}</span>
                  </div>
                </template>
              </div>
            </section>
          </v-col>

          <!-- 算力额度 (spec §9.1): 机构发放的额度余量; 无挂靠 = 不限额 -->
          <v-col cols="12" md="6">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">算力额度</span>
                <v-spacer />
                <span v-if="credits && !credits.unlimited" class="ln-num text-medium-emphasis">
                  1 额度 = 1 万 tokens
                </span>
              </div>
              <div class="page-section-body">
                <div v-if="!credits" class="text-medium-emphasis text-body-2 py-2">暂无额度信息</div>
                <div v-else-if="credits.unlimited" class="text-medium-emphasis text-body-2 py-2">
                  不限额 · 未挂靠机构任务的自治项目
                </div>
                <template v-else>
                  <div class="credit-remaining" :class="{ 'credit-remaining--empty': creditsExhausted }">
                    {{ fmtCredits(credits.credits_remaining) }}
                    <span class="credit-remaining__unit">额度剩余</span>
                  </div>
                  <div class="credit-bar mb-2">
                    <div
                      class="credit-bar__used"
                      :class="{ 'credit-bar__used--empty': creditsExhausted }"
                      :style="{ width: creditsUsedPct + '%' }"
                    />
                  </div>
                  <div class="ln-row">
                    <span class="ln-row-title">已用 / 共发放</span>
                    <v-spacer />
                    <span class="ln-num">
                      {{ fmtCredits(credits.credits_used) }} / {{ fmtCredits(credits.credits_total) }}
                    </span>
                  </div>
                  <div v-if="creditsExhausted" class="credit-exhausted mt-1">
                    额度已用完，芝士将无法继续运行，请联系机构续充
                  </div>
                  <div v-else class="text-caption text-medium-emphasis mt-2">
                    来自 {{ credits.grants.length }} 笔机构发放，按发放顺序扣减
                  </div>
                </template>
              </div>
            </section>
          </v-col>

          <!-- 成员 -->
          <v-col cols="12" md="6">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">成员</span>
              </div>
              <div class="page-section-body">
                <div v-if="!overview.members?.length" class="text-medium-emphasis text-body-2 py-2">暂无成员</div>
                <router-link
                  v-for="m in overview.members"
                  :key="m.handle"
                  class="ln-row ln-link"
                  :to="{
                    name: 'member',
                    params: { projectId: props.projectId, handle: m.handle },
                  }"
                >
                  <span class="ln-mini-avatar">{{ m.handle.slice(0, 1).toUpperCase() }}</span>
                  <span class="ln-row-title">@{{ m.handle }}</span>
                  <v-spacer />
                  <span class="ln-tag">{{ label(PROJECT_ROLE, m.role) }}</span>
                </router-link>
              </div>
            </section>
          </v-col>

          <!-- 收件箱 -->
          <v-col cols="12">
            <section class="page-section">
              <div class="page-section-head">
                <span class="page-section-title">收件箱 · 你的请求</span>
              </div>
              <div class="page-section-body">
                <div v-if="inbox.length === 0" class="text-medium-emphasis text-body-2 py-2">暂无请求</div>
                <div v-else>
                  <div v-for="item in inbox" :key="item.id" class="ln-inbox-row" :class="{ 'inbox-read': item.read }">
                    <div class="d-flex align-center ga-2 flex-wrap">
                      <span class="ln-tag">{{ label(NOTIF_KIND, item.kind) }}</span>
                      <span class="t-body" style="font-weight: 500; color: var(--ink)">{{ item.title }}</span>
                      <span v-if="item.source_handle" class="t-meta"> 来自 @{{ item.source_handle }} </span>
                      <v-spacer />
                      <v-btn
                        icon="mdi-thumb-up-outline"
                        size="x-small"
                        variant="text"
                        :color="item.feedback === 'up' ? 'primary' : undefined"
                        @click="onFeedback(item, 'up')"
                      />
                      <v-btn
                        icon="mdi-thumb-down-outline"
                        size="x-small"
                        variant="text"
                        :color="item.feedback === 'down' ? 'primary' : undefined"
                        @click="onFeedback(item, 'down')"
                      />
                      <v-btn v-if="!item.read" size="x-small" variant="text" class="c-muted" @click="onMarkRead(item)">
                        标记已读
                      </v-btn>
                      <span v-else class="d-inline-flex align-center ga-1 c-faint" style="font-size: 12px">
                        <span class="status-dot status-dot--ok" />已读
                      </span>
                    </div>
                    <div v-if="item.body" class="t-body c-muted mt-1">
                      {{ item.body }}
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </v-col>
        </v-row>
      </template>
    </v-container>
  </div>
</template>

<style scoped>
/* 内容区是侧栏 (--canvas) 上面那张 surface —— 和话题视图同一层关系。 */
.overview-page {
  background: var(--surface);
}

/* ---- 区块不是卡片 ----
   根面变白之后，「canvas 上铺白卡」这个手法既失效也不再需要：白底上套白框只
   是给白底加了个轮廓。区块划分改由留白 + eyebrow 小标题 + 一条顶部发丝线承担
   （分隔线，不是左竖条）。卡片留给列表里真正可拿起的对象。 */
.page-section {
  padding-top: 18px;
  margin-bottom: 10px;
  border-top: 1px solid var(--line);
}
.page-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
  margin-bottom: 4px;
}
/* eyebrow: 和页头的 .t-eyebrow 同一档，区块标题不再是一条卡片抬头。 */
.page-section-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--faint);
}
/* The one small amber flag on this page: "等你处理的事". */
.ln-accent-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
}
.ln-count {
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  background: var(--fill);
  padding: 1px 7px;
  border-radius: 8px;
}
.page-section-body {
  padding: 0;
}
.ln-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  padding: 2px 0;
  border-bottom: 1px solid var(--line);
}
.ln-row:last-child {
  border-bottom: none;
}
.ln-link {
  text-decoration: none;
  color: inherit;
  border-radius: 6px;
  margin: 0 -8px;
  padding: 2px 8px;
}
.ln-link:hover {
  background: var(--fill);
}
.ln-row-title {
  font-size: 14px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ln-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--faint);
}
.ln-dot-muted {
  background: var(--faint);
}
.ln-dot-warn {
  background: var(--warn);
}
.ln-dot-ink {
  background: var(--ink);
}
.ln-num {
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum';
  font-family: var(--font-mono);
  color: var(--faint);
}
.ln-tag {
  font-size: 12px;
  color: var(--muted);
  background: var(--fill);
  padding: 1px 8px;
  border-radius: 6px;
  white-space: nowrap;
}
.ln-mini-avatar {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--fill);
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
/* 区块里的次级分组。区块标题降成 eyebrow 之后它不能再用同一套字号字重，否则
   「其他成员待办」读起来和「等你处理的事」是同一级。 */
.ln-subhead {
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
  margin: 14px 0 4px;
}
.ln-group-label {
  margin: 6px 0 2px;
}
.ln-inbox-row {
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.ln-inbox-row:last-child {
  border-bottom: none;
}
.inbox-read {
  opacity: 0.55;
}
/* 算力额度: remaining number + used bar. Amber only when actually exhausted. */
.credit-remaining {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 26px;
  font-weight: 600;
  color: var(--ink);
  margin: 6px 0 10px;
}
.credit-remaining--empty {
  color: var(--warn);
}
.credit-remaining__unit {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 400;
  color: var(--faint);
  margin-left: 6px;
}
.credit-bar {
  height: 10px;
  /* 10px 高的进度条，两端本来就该是半圆 → --radius-pill（原来是 5px，不在阶梯上）。 */
  border-radius: var(--radius-pill);
  overflow: hidden;
  background: var(--fill);
}
.credit-bar__used {
  height: 100%;
  background: var(--ink);
  transition: width 0.3s ease;
}
.credit-bar__used--empty {
  background: var(--warn);
}
.credit-exhausted {
  font-size: 12.5px;
  color: var(--warn);
}

/* 贡献图: human vs AI split bar — neutral (ink vs faint), not amber. */
.contrib-bar {
  display: flex;
  height: 14px;
  border-radius: var(--radius-pill);
  overflow: hidden;
  background: var(--fill);
}
.contrib-seg {
  height: 100%;
  transition: width 0.3s ease;
}
.contrib-human {
  background: var(--ink);
}
.contrib-ai {
  background: var(--faint);
}
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot-human {
  background: var(--ink);
}
.dot-ai {
  background: var(--faint);
}
/* Rendered markdown for the 一页纸总结 (v-html → :deep). */
.md-content {
  font-size: 0.92rem;
  line-height: 1.65;
}
.md-content :deep(p) {
  margin: 0 0 8px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) {
  font-size: 1.05em;
  font-weight: 600;
  margin: 12px 0 6px;
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li::marker) {
  color: var(--faint);
}
.md-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
}
.md-content :deep(a:hover) {
  text-decoration: underline;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
