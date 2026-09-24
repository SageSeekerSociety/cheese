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
import { t } from '@/i18n'
import { topicPhaseBadge, topicShortId, topicStateBadge, topicTitle } from '@/lib/topicState'
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
const title = computed(() => topicTitle(props.topic))
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
    machineNotice.value = null
  }
)

// 这个房间能看到整台机器。算力选择器收进了 ⋯，这件事不能跟着收：它是权限，不是
// 设置，要一直看得见。选择器在菜单里也照常挂着（eager），由它告诉这里。
const machineNotice = ref<string | null>(null)

// 一行一个范围：次数 · token · 费用。输入 / 输出的拆分和费用的说明放在 title 里，
// 原来那十个大格子里有八个在一个新话题上都是 0。
function usageLine(u: UsageStats): string {
  return t('work.room.menu.usageLine', { turns: fmtNum(u.turns), tokens: fmtNum(u.total_tokens), cost: costLabel(u) })
}
function usageTitle(u: UsageStats): string {
  const split = t('work.room.menu.usageSplit', { input: fmtNum(u.input_tokens), output: fmtNum(u.output_tokens) })
  const note = costNote(u)
  return note ? `${split}\n${note}` : split
}

function toggleFocus() {
  usageOpen.value = false
  emit('toggle-focus')
}
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
      <!-- 桌面标题和状态沿同一基线排列，编号放在详情里。 -->
      <div class="topic-header__text">
        <span class="topic-header__title t-title" :title="title">{{ title }}</span>
        <span class="topic-header__meta">
          <!-- 全局那个房间没有「进行中 / 待验收」可言：它是项目本身，不是一件事。 -->
          <span v-if="isWorkTopic" class="pr-state" :class="state.cls">{{ state.label }}</span>
          <span v-if="machineNotice !== null" class="topic-header__machine" :title="machineNotice || undefined">
            <span class="status-dot status-dot--warn" />整台机器
          </span>
          <span v-if="!connected" class="topic-header__disconnected" role="status">未连接</span>
        </span>
      </div>

      <!-- 群聊感 (fusion-design §3): the roster, as a normal child of this row.
           芝士也在这份名册里（带 Agent 标），换 AI 队友就在它那一行上。 -->
      <TopicMembers v-if="isWorkTopic" :topic-id="topic.id" :project-members="members" :me="me" />

      <!-- 专注模式开着的时候，出口必须摆在外面：对话栏已经让开了，这一颗就是
           「你现在在专注模式里」的那句话。进去的入口在 ⋯ 里。 -->
      <v-btn
        v-if="mdAndUp && focus"
        icon="mdi-arrow-collapse"
        size="small"
        variant="text"
        class="topic-header__on"
        :title="t('work.room.menu.exitFocus')"
        :aria-label="t('work.room.menu.exitFocus')"
        @click="emit('toggle-focus')"
      />

      <!-- 这一行常驻的只有标题、状态、成员。其余的都是偶尔才用的，按「做一件事 /
           改一项设置 / 看一个数」分成三段：专注模式、运行环境、用量，编号垫在最底下。
           连接状态不在这里：连着是常态不用说，断了页头上自己会写「未连接」。
           eager：算力选择器要在菜单合着的时候就挂上，才能说出「整台机器」。 -->
      <v-menu v-model="usageOpen" :close-on-content-click="false" location="bottom end" eager>
        <template #activator="{ props: menuProps }">
          <v-btn
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="small"
            variant="text"
            color="medium-emphasis"
            :title="t('work.room.menu.more')"
            :aria-label="t('work.room.menu.more')"
          />
        </template>
        <v-card min-width="300" class="room-menu">
          <!-- 专注模式：面板占满工作区，隐藏对话栏 (spec §7.1)。手机上不存在——那儿
               永远只有一个窗格，没有第二栏可以让开。 -->
          <button v-if="mdAndUp" type="button" class="room-menu__row room-menu__row--action" @click="toggleFocus">
            <v-icon size="16">{{ focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand' }}</v-icon>
            <span>{{ focus ? t('work.room.menu.exitFocus') : t('work.room.menu.focus') }}</span>
          </button>
          <!-- 算力：这个话题的轮次在哪儿跑。它是话题的属性（发完第一条就锁死），
               不是某条消息的动作，所以不在输入区。 -->
          <div v-if="isWorkTopic" class="room-menu__row">
            <span class="room-menu__label">{{ t('work.room.menu.compute') }}</span>
            <TopicComputePicker :key="topic.id" :topic-id="topic.id" @machine-access="machineNotice = $event" />
          </div>
          <div class="room-menu__usage">
            <div class="room-menu__label">{{ t('work.room.menu.usage') }}</div>
            <div v-if="usageLoading" class="d-flex justify-center py-2">
              <v-progress-circular indeterminate color="primary" size="20" />
            </div>
            <template v-else>
              <div
                v-for="row in [
                  { label: t('work.room.menu.thisTopic'), u: topicUsage },
                  { label: t('work.room.menu.wholeProject'), u: projectUsage },
                ]"
                :key="row.label"
                class="usage-row"
                :title="row.u ? usageTitle(row.u) : undefined"
              >
                <span>{{ row.label }}</span>
                <span v-if="row.u" class="usage-row__value">{{ usageLine(row.u) }}</span>
                <span v-else class="usage-row__value">{{ t('work.room.menu.noUsage') }}</span>
              </div>
            </template>
          </div>
          <div v-if="isWorkTopic" class="room-menu__foot t-meta" :title="topic.id">#{{ shortId }}</div>
        </v-card>
      </v-menu>
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
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  padding: 0;
  background: none;
  border-bottom: 0;
}
.topic-header__text {
  display: flex;
  align-items: center;
  /* 这一块吃掉整行剩下的宽度，标题才有得截断；不写 min-width 的话 flex 子项
     以内容为最小宽度，右边的按钮会被挤出去。 */
  min-width: 0;
  flex: 1 1 auto;
  gap: 10px;
}
.topic-header__title {
  min-width: 0;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topic-header__meta {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 6px;
  line-height: 1.2;
  overflow: hidden;
  white-space: nowrap;
}
.topic-header--bar .topic-header__text {
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 3px;
}
.topic-header--bar .topic-header__title {
  max-width: 100%;
}
.topic-header__disconnected {
  color: var(--warn-ink);
  font-size: 12px;
  flex: 0 0 auto;
}
.topic-header__machine {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  color: var(--warn-ink);
  font-size: 12px;
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
  color: var(--muted);
  background: var(--fill);
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
/* ⋯ 菜单。一行一件事，行高和别处的菜单一样（36px），分段靠一条 --line。 */
.room-menu {
  padding-block: 4px;
}
.room-menu__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 36px;
  padding: 4px 16px;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
}
.room-menu__row--action {
  justify-content: flex-start;
  gap: 8px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.room-menu__row--action:hover {
  background: var(--fill);
}
.room-menu__label {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.room-menu__usage {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
  padding: 8px 16px;
  border-top: 1px solid var(--line);
}
.usage-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.usage-row__value {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.room-menu__foot {
  padding: 8px 16px 4px;
  border-top: 1px solid var(--line);
  color: var(--faint);
}
</style>
