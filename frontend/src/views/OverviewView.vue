<script setup lang="ts">
import type { Contributions, InboxItem, ProjectCredits, ProjectOverview, TopicRef } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

import {
  generateSummary,
  getContributions,
  getInbox,
  getOverview,
  getProject,
  getProjectCredits,
  markRead,
  sendFeedback,
} from '../api'
import { label, NOTIF_KIND, PROJECT_ROLE, TOPIC_STATUS } from '../labels'
import { myHandle } from '../me'

const props = defineProps<{ projectId: string }>()
const router = useRouter()

const ME = myHandle()

const overview = ref<ProjectOverview | null>(null)
const inbox = ref<InboxItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// 一页纸总结 (Feature A). Held separately so 生成/刷新 can update it in place.
const summary = ref<string>('')
const summaryLoading = ref(false)

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false }) as string)
}

async function onGenerateSummary() {
  summaryLoading.value = true
  error.value = null
  try {
    const res = await generateSummary(props.projectId)
    summary.value = res.summary ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '生成总结失败'
  } finally {
    summaryLoading.value = false
  }
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

// Back to wherever you came from (the workspace, via a notification/action
// card or the rail), with a workspace fallback for a deep link — same pattern
// as the settings and member pages.
function goBack() {
  if (window.history.state?.back != null) router.back()
  else router.push({ name: 'workspace-project', params: { projectId: props.projectId } })
}

watch(() => props.projectId, load)
onMounted(load)
</script>

<template>
  <div class="overview-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 1100px">
      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable">
        {{ error }}
      </v-alert>

      <template v-else-if="overview">
        <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" class="mb-3 px-1" @click="goBack">
          返回
        </v-btn>

        <div class="mb-6">
          <div class="t-eyebrow mb-1">项目总览</div>
          <h1 class="t-page-title">{{ overview.name }}</h1>
        </div>

        <!-- 一页纸总结 (Feature A) -->
        <section class="ln-section">
          <div class="ln-section-head">
            <span class="ln-section-title">一页纸总结</span>
            <v-spacer />
            <v-btn
              size="small"
              variant="text"
              class="c-muted"
              :prepend-icon="summary ? 'mdi-refresh' : 'mdi-creation'"
              :loading="summaryLoading"
              @click="onGenerateSummary"
            >
              {{ summary ? '刷新' : '生成总结' }}
            </v-btn>
          </div>
          <div class="ln-body">
            <div v-if="summary" class="md-content" v-html="renderMarkdown(summary)" />
            <div v-else class="c-faint t-body py-2">芝士还没写总结，点生成。</div>
          </div>
        </section>

        <!-- 等你处理的事 (subtle accent, not a hero block) -->
        <section class="ln-section">
          <div class="ln-section-head">
            <span class="ln-accent-dot" />
            <span class="ln-section-title">等你处理的事</span>
            <span v-if="myWaiting.length" class="ln-count">{{ myWaiting.length }}</span>
          </div>
          <div class="ln-body">
            <div v-if="myWaiting.length === 0" class="c-faint t-body py-2">没有需要你处理的事</div>
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
            <section class="ln-section h-100">
              <div class="ln-section-head">
                <span class="ln-section-title">里程碑</span>
              </div>
              <div class="ln-body">
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
            <section class="ln-section h-100">
              <div class="ln-section-head">
                <span class="ln-section-title">话题</span>
                <v-spacer />
                <span class="ln-num text-medium-emphasis">共 {{ overview.topic_count }}</span>
              </div>
              <div class="ln-body">
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
            <section class="ln-section h-100">
              <div class="ln-section-head">
                <span class="ln-section-title">贡献 · 人 / AI</span>
              </div>
              <div class="ln-body">
                <div v-if="contribTotal === 0" class="text-medium-emphasis text-body-2 py-2">暂无贡献记录</div>
                <template v-else>
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
                  <div class="text-caption text-medium-emphasis mt-2">人指挥、AI 执行，各自统计</div>
                </template>
              </div>
            </section>
          </v-col>

          <!-- 算力额度 (spec §9.1): 机构发放的额度余量; 无挂靠 = 不限额 -->
          <v-col cols="12" md="6">
            <section class="ln-section h-100">
              <div class="ln-section-head">
                <span class="ln-section-title">算力额度</span>
                <v-spacer />
                <span v-if="credits && !credits.unlimited" class="ln-num text-medium-emphasis">
                  1 额度 = 1 万 tokens
                </span>
              </div>
              <div class="ln-body">
                <div v-if="!credits" class="text-medium-emphasis text-body-2 py-2">额度信息暂不可用</div>
                <div v-else-if="credits.unlimited" class="text-medium-emphasis text-body-2 py-2">
                  不限额 · 自治项目（未挂靠机构任务，链接题目后按资源包计量）
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
                    额度已用完——芝士的新一轮会被拒绝，请联系机构续充
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
            <section class="ln-section h-100">
              <div class="ln-section-head">
                <span class="ln-section-title">成员</span>
              </div>
              <div class="ln-body">
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
            <section class="ln-section">
              <div class="ln-section-head">
                <span class="ln-section-title">收件箱 · 你的请求</span>
              </div>
              <div class="ln-body">
                <div v-if="inbox.length === 0" class="text-medium-emphasis text-body-2 py-2">收件箱是空的</div>
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
.overview-page {
  background: var(--canvas);
}

/* ---- Calm, neutral sections: hairline separators, no shadow ---- */
.ln-section {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  margin-bottom: 16px;
}
.ln-section.h-100 {
  height: 100%;
}
.ln-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
}
.ln-section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
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
.ln-body {
  padding: 6px 18px 14px;
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
.ln-subhead {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--faint);
  margin: 12px 0 4px;
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
  border-radius: 5px;
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
  border-radius: 7px;
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
  border-radius: 4px;
  font-size: 0.88em;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
