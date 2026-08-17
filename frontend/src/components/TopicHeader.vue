<script setup lang="ts">
// 话题头部: ONE bar across the whole topic view.
//
// It replaces three separate headers that used to sit side by side and never
// quite line up: ChatPanel's PR header (title · #id · 状态), DocPanel's toolbar
// (「文档」· 专注 · 五个抽屉图标), and the member roster — which was an absolutely
// positioned overlay pinned with two magic numbers (`height: 45px` to match the
// chat header's computed height, `right: calc(… + 92px)` to sit left of the doc
// toolbar's buttons). Both numbers are gone: the roster is a normal flex child
// of this row now, so there is nothing left to align it against.
//
// 资源 lands here too. It used to be the fifth drawer, re-fetching every 20
// seconds for as long as it was open; usage numbers do not move that fast, and
// nobody watches them. Now they load once, when the popover is opened.
import type { ProjectMemberRow, Topic, UsageStats } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import { getProjectUsage, getTopicUsage } from '@/api'
import TopicMembers from '@/components/TopicMembers.vue'
import { topicShortId, topicStateBadge } from '@/lib/topicState'
import { costLabel, costNote, fmtNum } from '@/lib/usageFormat'

const props = defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  me: string
  /** ChatPanel's live socket state — the dot that says 已连接 / 未连接. */
  connected: boolean
  /** 专注模式 (spec §7.1): the panel spans the workspace, the chat is hidden. */
  focus: boolean
}>()

const emit = defineEmits<{ (e: 'toggle-focus'): void }>()

const state = computed(() => topicStateBadge(props.topic.status))
const shortId = computed(() => topicShortId(props.topic.id))
// 项目本体 is not a work topic — it has no id badge and no roster.
const isWorkTopic = computed(() => props.topic.kind !== 'root')

// ---- 用量 popover (was the 资源 drawer) ----
const usageOpen = ref(false)
const usageLoading = ref(false)
const topicUsage = ref<UsageStats | null>(null)
const projectUsage = ref<UsageStats | null>(null)

async function loadUsage() {
  const tid = props.topic.id
  const pid = props.topic.project_id
  if (!tid || !pid) return
  usageLoading.value = true
  try {
    const [tu, pu] = await Promise.all([getTopicUsage(tid), getProjectUsage(pid)])
    if (props.topic.id !== tid) return
    topicUsage.value = tu
    projectUsage.value = pu
  } catch {
    // Best-effort; the popover just shows 暂无数据.
  } finally {
    if (props.topic.id === tid) usageLoading.value = false
  }
}

// Opening it is the only thing that fetches. No timer: 20 秒轮询 was buying
// staleness nobody was watching for.
watch(usageOpen, (open) => {
  if (open) void loadUsage()
})

watch(
  () => props.topic.id,
  () => {
    topicUsage.value = null
    projectUsage.value = null
    usageOpen.value = false
  }
)
</script>

<template>
  <div class="topic-header">
    <span class="topic-header__title t-title">{{ topic.title }}</span>
    <span v-if="isWorkTopic" class="topic-header__num t-meta">#{{ shortId }}</span>
    <span class="pr-state" :class="state.cls">{{ state.label }}</span>
    <span
      class="status-dot"
      :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
      :title="connected ? '已连接' : '未连接'"
    />

    <v-spacer />

    <!-- 群聊感 (fusion-design §3): the roster, as a normal child of this row. -->
    <TopicMembers v-if="isWorkTopic" :topic-id="topic.id" :project-members="members" :me="me" />

    <!-- 用量: was the 资源 drawer. -->
    <v-menu v-model="usageOpen" :close-on-content-click="false" location="bottom end">
      <template #activator="{ props: menuProps }">
        <v-btn
          v-bind="menuProps"
          icon="mdi-chart-box-outline"
          size="small"
          variant="text"
          class="c-muted"
          title="用量"
        />
      </template>
      <v-card min-width="280" class="usage-card">
        <div v-if="usageLoading" class="d-flex justify-center py-6">
          <v-progress-circular indeterminate color="primary" size="24" />
        </div>
        <div v-else class="pa-3">
          <div
            v-for="row in [
              { label: '本话题', u: topicUsage },
              { label: '全项目', u: projectUsage },
            ]"
            :key="row.label"
            class="mb-4"
          >
            <div class="t-eyebrow mb-2">{{ row.label }}</div>
            <div v-if="row.u" class="usage-grid">
              <div class="usage-cell">
                <div class="usage-num">{{ fmtNum(row.u.turns) }}</div>
                <div class="t-meta">轮次</div>
              </div>
              <div class="usage-cell">
                <div class="usage-num">{{ fmtNum(row.u.total_tokens) }}</div>
                <div class="t-meta">总 token</div>
              </div>
              <div class="usage-cell">
                <div class="usage-num">{{ fmtNum(row.u.input_tokens) }}</div>
                <div class="t-meta">输入</div>
              </div>
              <div class="usage-cell">
                <div class="usage-num">{{ fmtNum(row.u.output_tokens) }}</div>
                <div class="t-meta">输出</div>
              </div>
              <div class="usage-cell">
                <div class="usage-num" :title="costNote(row.u)">{{ costLabel(row.u) }}</div>
                <div class="t-meta">费用</div>
              </div>
            </div>
            <div v-else class="t-meta">暂无数据</div>
            <div v-if="row.u && costNote(row.u)" class="t-meta mt-1">
              {{ costNote(row.u) }}
            </div>
          </div>
        </div>
      </v-card>
    </v-menu>

    <!-- 专注模式: 面板占满工作区，隐藏对话栏 (spec §7.1) -->
    <v-btn
      :icon="focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
      size="small"
      variant="text"
      :class="focus ? 'topic-header__on' : 'c-muted'"
      :title="focus ? '退出专注' : '专注模式（面板全幅）'"
      @click="emit('toggle-focus')"
    />
  </div>
</template>

<style scoped>
.topic-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  min-height: var(--app-page-header-height, 48px);
  padding: 0 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.topic-header__title {
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topic-header__num {
  font-weight: 400;
  flex: 0 0 auto;
}
.topic-header__on {
  color: var(--ink);
}
/* 状态标 — semantic for 进行中, muted otherwise. Same three faces as the chat
   header still shows for 私聊 / 本体; the labels come from lib/topicState.ts. */
.pr-state {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 6px;
}
.pr-state--open {
  /* --surface, not #fff: the ground (--ok) lightens on dark (#3FBF7F), where
     white ink drops to 2.34:1. --surface IS #fff in light, so the badge looks
     exactly as it does today, and flips to near-black ink on dark. */
  color: var(--surface);
  background: var(--ok);
}
.pr-state--merged {
  color: var(--muted);
  background: var(--fill);
}
.pr-state--draft {
  color: var(--faint);
  background: var(--fill);
}
.usage-card {
  border: 1px solid var(--line);
}
.usage-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.usage-cell {
  background: var(--fill);
  border-radius: 8px;
  padding: 8px 12px;
}
.usage-num {
  font-family: var(--font-mono);
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
</style>
