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
import type { TopicPhase } from '@/lib/topicState'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { getProjectUsage, getTopicUsage } from '@/api'
import TopicComputePicker from '@/components/TopicComputePicker.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import { topicPhaseBadge, topicShortId, topicStateBadge } from '@/lib/topicState'
import { costLabel, costNote, fmtNum } from '@/lib/usageFormat'

const props = defineProps<{
  topic: Topic
  /** 话题此刻处在哪一段 — 施工中 / 待验收 / 交付中 / 已采纳, computed above this
   * component because it folds together the topic's status, its live turn and
   * its accept card. Absent (私聊 / 项目本体) falls back to the status alone. */
  phase?: TopicPhase
  members: ProjectMemberRow[]
  me: string
  /** ChatPanel's live socket state — the dot that says 已连接 / 未连接. */
  connected: boolean
  /** 专注模式 (spec §7.1): the panel spans the workspace, the chat is hidden. */
  focus: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-focus'): void
  (e: 'open-topic', topicId: string): void
  // 这个话题换了 AI 队友。对话栏要重拉名册——它显示的 AI 名字来自那份名册。
}>()

const { mdAndUp } = useDisplay()

// 头部常驻状态条 (规则 4): where this topic stands, always on screen. It used to
// read the topic row's `status` alone, which knows only 归档 —— 「待验收」 and
// 「交付中」 were visible solely by scrolling the conversation to the accept box,
// so a reviewer could sit in a topic that was waiting on them and see 「进行中」.
const state = computed(() => (props.phase ? topicPhaseBadge(props.phase) : topicStateBadge(props.topic.status)))
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
  <!-- 手机上这一行不长在页面上，它**就是**顶栏那一格的内容（Teleport 进去）。
       手机上只有一条顶栏，从不卸载：话题页自己再画一条，两条横条交替出现的时候
       v-main 的 padding 会滑一下，整页跟着抖。← 由顶栏按路由的 backTo 出，
       所以这里不再自己画一个。 -->
  <!-- The mobile target is absent on desktop. Remount at the breakpoint so
       Teleport resolves it after App has rendered the new mobile bar. -->
  <Teleport :key="String(mdAndUp)" to="#app-bar-slot" :disabled="mdAndUp" defer>
    <div class="topic-header" :class="{ 'topic-header--bar': !mdAndUp }">
      <!-- 手机上标题独占一行，编号和状态退到下面那条小字：横着平铺的话，标题在
         390px 上只剩七个字，而它才是你要看的那个。桌面上宽度够，一行摆开更快读。 -->
      <div class="topic-header__text">
        <span class="topic-header__title t-title">{{ topic.title }}</span>
        <span v-if="!mdAndUp" class="topic-header__meta t-meta">
          <template v-if="isWorkTopic">#{{ shortId }}</template>
          <span class="pr-state" :class="state.cls">{{ state.label }}</span>
          <template v-if="!connected">未连接</template>
        </span>
      </div>
      <template v-if="mdAndUp">
        <span v-if="isWorkTopic" class="topic-header__num t-meta">#{{ shortId }}</span>
        <span class="pr-state" :class="state.cls">{{ state.label }}</span>
        <span
          class="status-dot"
          :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
          :title="connected ? '已连接' : '未连接'"
        />
      </template>

      <v-spacer />

      <!-- 算力：这个话题的轮次在哪儿跑。它以前住在输入区的动作行里，可那一行是
           「这条消息」的动作，而算力发完第一条就锁死了——是话题的属性，属于这一行。
           手机上这一行没有它的位置，它浮在对话上方（TopicChatColumn）。 -->
      <TopicComputePicker v-if="mdAndUp && isWorkTopic" :key="topic.id" :topic-id="topic.id" />

      <!-- 群聊感 (fusion-design §3): the roster, as a normal child of this row.
           芝士也在这份名册里（带 Agent 标），换 AI 队友就在它那一行上。 -->
      <TopicMembers v-if="isWorkTopic" :topic-id="topic.id" :project-members="members" :me="me" />

      <!-- 用量: was the 资源 drawer. -->
      <v-menu v-model="usageOpen" :close-on-content-click="false" location="bottom end">
        <template #activator="{ props: menuProps }">
          <v-btn
            v-bind="menuProps"
            icon="mdi-chart-box-outline"
            size="small"
            variant="text"
            color="medium-emphasis"
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
                  <div class="t-meta">运行次数</div>
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

      <!-- 专注模式: 面板占满工作区，隐藏对话栏 (spec §7.1)。手机上不存在——那儿
         永远只有一个窗格，没有第二栏可以让开。 -->
      <v-btn
        v-if="mdAndUp"
        :icon="focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand'"
        size="small"
        variant="text"
        :class="focus ? 'topic-header__on' : 'c-muted'"
        :title="focus ? '退出专注模式' : '专注模式'"
        @click="emit('toggle-focus')"
      />
    </div>
  </Teleport>
</template>

<style scoped>
.topic-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  /* 页头基线：和左边侧栏顶栏、上面内容区页头是同一条线，所以高度和底线都读同一
     个 token。这里曾经是 min-height，成员头像那一列一长就能把这条头顶高，两条线
     于是错开——顶栏的高度不是内容说了算的。 */
  height: var(--app-page-header-height);
  padding: 0 12px;
  background: var(--surface);
  border-bottom: var(--app-page-header-rule);
}
/* 填进顶栏的那一份不画自己的高度、底色和底线——那三样归顶栏。 */
.topic-header--bar {
  height: 100%;
  padding: 0;
  background: none;
  border-bottom: 0;
}
.topic-header__text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* 这一块吃掉整行剩下的宽度，标题才有得截断；不写 min-width 的话 flex 子项
     以内容为最小宽度，右边的按钮会被挤出去。 */
  min-width: 0;
  flex: 1 1 auto;
  gap: 1px;
}
.topic-header__title {
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topic-header__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.2;
  overflow: hidden;
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
/* 待验收 = 有人在等你。--warn 的三件套里文字用 -ink、底用 -wash：把 mark 色
   (--warn) 拿来当文字在浅色主题下只有 2.34:1，读不动。 */
.pr-state--reviewing {
  color: var(--warn-ink);
  background: var(--warn-wash);
}
/* 施工中 / 交付中 = 机器在忙，不需要你做什么，所以是中性的陈述而不是招手。 */
.pr-state--working,
.pr-state--delivering {
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
