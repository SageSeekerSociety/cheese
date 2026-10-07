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
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectMemberRow, Topic, UsageStats } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { useNavigation } from '@/composables/useNavigation'

import { getProjectUsage, getTopicUsage } from '@/api'
import { menuActionOf, useCommands } from '@/commands'
import { archiveTopic, topicActions } from '@/commands/topicActions'
import BaseButton from '@/components/base/BaseButton.vue'
import ChannelDetailsDialog from '@/components/channel/ChannelDetailsDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import MobileActionSheet from '@/components/common/MobileActionSheet.vue'
import ChannelNotifyMenu from '@/components/room/ChannelNotifyMenu.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import TopicUsageSummary from '@/components/TopicUsageSummary.vue'
import { t } from '@/i18n'
import { topicShortId, topicStateBadge, topicTitle } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  me: string
  /** ChatPanel's live socket state — the dot that says 已连接 / 未连接. */
  connected: boolean
  /** 专注模式 (spec §7.1): the panel spans the workspace, the chat is hidden. */
  focus: boolean
  /** 专注模式只在面板和对话并排开着时成立。 */
  canFocus?: boolean
  /** 右侧面板是不是开着——「概览」那颗开关读它。 */
  panelOpen?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-focus'): void
  (e: 'open-topic', topicId: string): void
  (e: 'rename', title: string): void
  (e: 'toggle-panel'): void
  // 这个话题换了 AI 队友。对话栏要重拉名册——它显示的 AI 名字来自那份名册。
}>()

const { mdAndUp } = useDisplay()

// 房间自己的生命周期只在不寻常时说一句（已归档 / 草稿），和侧栏那一行同一个规矩。
// 房间没有「在干活 / 待审阅」这种状态：干活的是成员（输入框下面那一行），待审阅
// 的是卡（卡上、看板上）。
const state = computed(() =>
  props.topic.status === 'archived' || props.topic.status === 'draft' ? topicStateBadge(props.topic.status) : null
)
const shortId = computed(() => topicShortId(props.topic.id))
const title = computed(() => topicTitle(props.topic))
// 「综合」是项目本身，不是一件事：没有编号，也没有「进行中」这类状态。
const isWorkTopic = computed(() => props.topic.kind !== 'root')
// 我对这个频道的通知档位（铃铛）。
const store = useWorkspaceStore()
// ---- 用量 popover (was the 资源 drawer) ----
const usageOpen = ref(false)
const usageLoading = ref(false)
const topicUsage = ref<UsageStats | null>(null)
const projectUsage = ref<UsageStats | null>(null)

// 指针移到 ⋯ 上就开始取，点开时多半已经到了；刚取过的（同一个话题、十秒以内）
// 不再取第二遍——悬停一次紧接着点开是常态。取的时候手里的旧数照样显示，只有
// 一个数都还没有时才画占位条（TopicUsageSummary），卡片从一出来就是最终尺寸。
// 没有定时器：20 秒轮询买来的新鲜度没人在看。
const USAGE_FRESH_MS = 10_000
let usageFor: string | null = null
let usageAt = 0
async function loadUsage() {
  const tid = props.topic.id
  const pid = props.topic.project_id
  if (!tid || !pid) return
  if (usageLoading.value || (usageFor === tid && Date.now() - usageAt < USAGE_FRESH_MS)) return
  usageLoading.value = true
  try {
    const [tu, pu] = await Promise.all([getTopicUsage(tid), getProjectUsage(pid)])
    if (props.topic.id !== tid) return
    topicUsage.value = tu
    projectUsage.value = pu
    usageFor = tid
    usageAt = Date.now()
  } catch {
    // Best-effort; the popover just shows 暂无数据.
  } finally {
    usageLoading.value = false
  }
}

watch(usageOpen, (open) => {
  if (open) void loadUsage()
})
// 换了话题，上一个话题的数不能挂在这一个上。
watch(
  () => props.topic.id,
  () => {
    topicUsage.value = null
    projectUsage.value = null
    usageFor = null
  }
)

// 有 AI 队友能访问整台机器。工作电脑写在成员名册里，这件事不能跟着收进名册：它是
// 权限，不是设置，要一直看得见。名册读到了就告诉这里。
const machineNotice = ref<string | null>(null)

function toggleFocus() {
  usageOpen.value = false
  emit('toggle-focus')
}

// 手机上话题列表没有行尾那颗 ⋯，改名、归档原本只有长按那一行才找得到。⋯ 面板里
// 放的是同一份（topicActions），只是这里重命名另起一页。
const nav = useNavigation()
const renaming = ref(false)
const draftTitle = ref('')

function startRename() {
  // 还没名字的话题从空白开始改：占位标题不是谁起的名字。
  draftTitle.value = props.topic.title
  renaming.value = true
}

function saveRename() {
  const next = normalizeTopicTitle(draftTitle.value, props.topic.title)
  renaming.value = false
  if (next) emit('rename', next)
}

// 同一份话题操作：⋯ 面板里是一行一行的菜单，命令面板里是「操作」。
// `topicActions` 要的是整台 router（复制链接靠它解析、新建任务/归档靠它跳），从
// `nav.router` 上取；宿主没装路由时没有去处，这份菜单就空着——和别处「没有路由就
// 少画一点」同一个道理。
function roomCommands() {
  if (!nav) return []
  return topicActions(props.topic, nav.router, { rename: startRename })
}
const roomActions = computed<MenuAction[]>(() => roomCommands().map(menuActionOf))

// 频道详情：点页头的频道名打开。
const detailsOpen = ref(false)
const managesProject = computed(() => store.openedProject?.can_manage_members === true)
function archiveFromDetails() {
  detailsOpen.value = false
  if (nav) void archiveTopic(props.topic, nav.router)
}
function leaveFromDetails() {
  detailsOpen.value = false
  void store.setJoined(props.topic.id, false)
}
useCommands(roomCommands)
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
        <button
          type="button"
          class="topic-header__title topic-header__title--button t-title"
          :title="title"
          :aria-label="t('work.channelDetails.open', { name: title })"
          @click="detailsOpen = true"
        >
          <v-icon
            v-if="topic.members_only"
            size="14"
            class="topic-header__lock"
            icon="mdi-lock-outline"
            :title="t('work.channel.privateTip')"
            :aria-label="t('work.channel.privateTip')"
          />{{ title }}
        </button>
        <span v-if="mdAndUp && topic.description" class="topic-header__description" :title="topic.description">{{
          topic.description
        }}</span>
        <span class="topic-header__meta">
          <!-- 全局那个房间没有「进行中 / 待验收」可言：它是项目本身，不是一件事。 -->
          <span v-if="isWorkTopic && state" class="pr-state" :class="state.cls">{{ state.label }}</span>
          <span v-if="machineNotice !== null" class="topic-header__machine" :title="machineNotice || undefined">
            <span class="status-dot status-dot--warn" />{{ t('work.roomMachine.wholeMachine') }}
          </span>
          <span v-if="!connected" class="topic-header__disconnected" role="status">{{
            t('work.room.header.disconnected')
          }}</span>
        </span>
      </div>

      <!-- 群聊感 (fusion-design §3): the roster, as a normal child of this row.
           芝士也在这份名册里（带 Agent 标），换 AI 队友就在它那一行上。 -->
      <TopicMembers
        :topic-id="topic.id"
        :can-manage="topic.can_manage === true"
        :can-invite="topic.joined === true"
        :general="!isWorkTopic"
        :project-id="topic.project_id"
        :project-members="members"
        :me="me"
        @machine-access="machineNotice = $event"
      />

      <ChannelDetailsDialog
        v-if="detailsOpen"
        v-model="detailsOpen"
        :topic="topic"
        :can-manage="topic.can_manage === true"
        :manages-project="managesProject"
        :level="store.levelOf(topic.id)"
        :muted-until="store.mutedUntil(topic.id)"
        @rename="(next) => emit('rename', next)"
        @describe="(text) => store.describe(topic.id, text)"
        @set-private="(membersOnly) => store.setMembersOnly(topic.id, membersOnly)"
        @archive="archiveFromDetails"
        @unarchive="store.unarchive(topic.id)"
        @leave="leaveFromDetails"
        @notify="(level, until) => store.setNotifyLevel(topic.id, level, until)"
      />

      <ChannelNotifyMenu
        v-if="topic.joined && topic.status !== 'archived'"
        :level="store.levelOf(topic.id)"
        :muted-until="store.mutedUntil(topic.id)"
        @set="(level, until) => store.setNotifyLevel(topic.id, level, until)"
      />

      <!-- 专注模式开着的时候，出口必须摆在外面：对话栏已经让开了，这一颗就是
           「你现在在专注模式里」的那句话。进去的入口在 ⋯ 里。 -->
      <BaseButton
        v-if="mdAndUp && focus"
        icon="mdi-arrow-collapse"
        size="sm"
        :title="t('work.room.menu.exitFocus')"
        :aria-label="t('work.room.menu.exitFocus')"
        @click="emit('toggle-focus')"
      />

      <!-- 右侧面板的开关。手机上面板是页签里的一格，没有这颗。 -->
      <PanelToggle v-if="mdAndUp" :open="!!panelOpen" @toggle="emit('toggle-panel')" />

      <!-- 这一行常驻的只有标题、状态、成员。其余的都是偶尔才用的，按「做一件事 /
           看一个数」分成两段：专注模式、用量，编号垫在最底下。工作电脑在成员名册里。
           连接状态不在这里：连着是常态不用说，断了页头上自己会写「未连接」。 -->
      <v-menu v-if="mdAndUp" v-model="usageOpen" :close-on-content-click="false" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="sm"
            class="tap-target"
            :title="t('work.room.menu.more')"
            :aria-label="t('work.room.menu.more')"
            @mouseenter="loadUsage"
          />
        </template>
        <v-card min-width="300" class="room-menu">
          <!-- 专注模式：面板占满工作区，隐藏对话栏 (spec §7.1)。手机上不存在——那儿
               永远只有一个窗格，没有第二栏可以让开；平板横放那一档里对话永远占满
               整宽、面板才是那只浮层，所以专注在这里也没有位置。 -->
          <button
            v-if="mdAndUp && canFocus"
            type="button"
            class="room-menu__row room-menu__row--action"
            @click="toggleFocus"
          >
            <v-icon size="16">{{ focus ? 'mdi-arrow-collapse' : 'mdi-arrow-expand' }}</v-icon>
            <span>{{ focus ? t('work.room.menu.exitFocus') : t('work.room.menu.focus') }}</span>
          </button>
          <TopicUsageSummary
            class="room-menu__usage"
            :loading="usageLoading"
            :topic-usage="topicUsage"
            :project-usage="projectUsage"
            :short-id="isWorkTopic ? shortId : null"
            :topic-id="topic.id"
          />
        </v-card>
      </v-menu>
      <!-- 手机上同一块内容从底部升起，和别的手机菜单一样（设计系统 §10.4）。 -->
      <template v-else>
        <BaseButton
          icon="mdi-dots-horizontal"
          size="sm"
          class="tap-target"
          :title="t('work.room.menu.more')"
          :aria-label="t('work.room.menu.more')"
          @click="usageOpen = true"
        />
        <MobileActionSheet v-model="usageOpen" :actions="roomActions">
          <TopicUsageSummary
            :loading="usageLoading"
            :topic-usage="topicUsage"
            :project-usage="projectUsage"
            :short-id="isWorkTopic ? shortId : null"
            :topic-id="topic.id"
          />
        </MobileActionSheet>
        <AdaptiveDialog
          v-model="renaming"
          :title="t('work.room.menu.renameTitle')"
          :primary-label="t('global.save')"
          :primary-disabled="!draftTitle.trim()"
          @primary="saveRename"
        >
          <v-text-field
            v-model="draftTitle"
            :label="t('work.room.menu.topicName')"
            :maxlength="TOPIC_TITLE_MAX_LENGTH"
            :counter="TOPIC_TITLE_MAX_LENGTH"
            persistent-counter
            autocomplete="off"
            autofocus
            @keyup.enter="saveRename"
          />
        </AdaptiveDialog>
      </template>
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
.topic-header__title--button {
  padding: 0;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}
.topic-header__title--button:hover {
  color: var(--accent-ink);
}
.topic-header__title {
  min-width: 0;
  /* 行盒不在这里定：这一格和话题列表的行共用 .t-title，也就共用它的 --lh-15
     （21px）。原先这里压成 1.3（15px 字号 → 19.5px），比字身还矮，标题又是
     overflow: hidden，g / y 这些下伸的字母下缘被切掉约 0.75px。高度是字号阶梯
     的属性，不在调用点另定一个数（docs/design-system.md §3.2）。 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 频道说明：标题后面一句，比标题弱，挤不下就截断——完整的在 title 里。 */
.topic-header__description {
  min-width: 0;
  flex: 0 1 auto;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
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
/* 手机顶栏上它是一颗按钮，手指要点得中：上下撑到 44px。不用 .tap-target，那一层
   伪元素会被标题自己截断用的 overflow: hidden 切掉。 */
.topic-header--bar .topic-header__title--button {
  padding-block: 12px;
}
/* 私密频道：标题前一把锁，和侧栏那一行同一个记号。 */
.topic-header__lock {
  margin-right: 4px;
  color: var(--muted);
  vertical-align: -1px;
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
.pr-state--merged {
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
.room-menu__usage {
  margin-top: 4px;
  border-top: 1px solid var(--line);
}
</style>
