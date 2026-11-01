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
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { useQuery } from '@tanstack/vue-query'

import { useNavigation } from '@/composables/useNavigation'
import { useTopicUsage } from '@/composables/useTopicUsage'

import { menuActionOf, useCommands } from '@/commands'
import { archiveTopic, topicActions } from '@/commands/topicActions'
import BaseButton from '@/components/base/BaseButton.vue'
import ChannelDetailsDialog from '@/components/channel/ChannelDetailsDialog.vue'
import DeleteChannelDialog from '@/components/channel/DeleteChannelDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import ChannelNotifyMenu from '@/components/room/ChannelNotifyMenu.vue'
import PanelToggle from '@/components/room/PanelToggle.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import TopicUsageSummary from '@/components/TopicUsageSummary.vue'
import { t } from '@/i18n'
import { routeIds } from '@/lib/addresses'
import { mayIn, roleName } from '@/lib/projectPermissions'
import { topicShortId, topicStateBadge, topicTitle } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { queryClient } from '@/query/client'
import { projectQuery, projectRolesQuery } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  me: string
  /** ChatPanel's live socket state — the dot that says 已连接 / 未连接. */
  connected: boolean
  /** 右侧面板是不是开着——「概览」那颗开关读它。 */
  panelOpen?: boolean
}>()

const emit = defineEmits<{
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
const menuOpen = ref(false)
// 取数（取哪两份、什么时候算新鲜）在 `composables/useTopicUsage.ts` 那一层。
const {
  loading: usageLoading,
  topic: topicUsage,
  project: projectUsage,
  load: loadUsage,
} = useTopicUsage({ topicId: () => props.topic.id, projectId: () => props.topic.project_id })

watch(menuOpen, (open) => {
  if (open) void loadUsage()
})

// ⋯ 里放的是侧栏那一行同一个频道操作菜单（commands/topicActions.ts）：两处各写一份
// 就会各长各的。桌面和手机都是这一份，桌面是下拉菜单、手机是底部升起的面板
// （AdaptiveMenu）——原先桌面上这颗 ⋯ 只开用量，改名、归档只有手机上找得到，等于
// 桌面版够不着（清单第 1、3 条）。用量本来是这颗 ⋯ 的全部内容，现在它退成菜单顶上
// 的一段（#desktopHeader / #header），照旧在指针移上来时先取。
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
  return topicActions(props.topic, nav.router, { rename: startRename, remove: () => (deleteOpen.value = true) })
}
const roomActions = computed<MenuAction[]>(() => roomCommands().map(menuActionOf))

// 频道详情：点页头的频道名打开。管理者是谁从页头名册那里来。
const detailsOpen = ref(false)
// 成员名单开着没有。名单自己长在成员名册那一块上，详情只叫它开（清单第 5 条）。
const membersOpen = ref(false)
const managerName = ref<string | null>(null)
// 我在这个项目里能做什么：详情或名单打开时才问，和工作区别处读同一份项目（`query/project`）。
const projectRead = useQuery(
  computed(() => ({
    ...projectQuery(props.topic.project_id),
    enabled: detailsOpen.value || membersOpen.value,
  })),
  queryClient
)
const managesProject = computed(() => mayIn(projectRead.data.value, 'manage_channels'))
// 项目的角色：详情里选「谁能发言」用，打开详情时才问（#4795）。
const rolesRead = useQuery(
  computed(() => ({ ...projectRolesQuery(props.topic.project_id), enabled: detailsOpen.value })),
  queryClient
)
const channelRoles = computed(() => (rolesRead.data.value ?? []).map((r) => ({ key: r.key, name: roleName(r) })))
// 不管频道也能拉人：人在这个频道里，角色又允许拉人进自己所在的频道。
// 项目还没读到时先按在不在频道里给，后端加人时再判一次。
const bringsPeopleIn = computed(
  () =>
    props.topic.joined === true && (projectRead.data.value == null || mayIn(projectRead.data.value, 'create_channels'))
)
function archiveFromDetails() {
  detailsOpen.value = false
  if (nav) void archiveTopic(props.topic, nav.router)
}
function leaveFromDetails() {
  detailsOpen.value = false
  void store.setJoined(props.topic.id, false)
}
// 详情先关、名单再开：两张浮层叠在一起，关上面那张时下面那张会跟着晃一下。
function openMembersFromDetails() {
  detailsOpen.value = false
  membersOpen.value = true
}

// 删除频道（清单第 2 条）：⋯ 菜单和详情「设置」都开到这张确认框上，真正删的是
// store（它删完把侧栏那一行去掉）。删成了就走——内容是刚被删掉的那个频道，留着
// 只会是一页空壳或者一个 404。
const deleteOpen = ref(false)
const deleting = ref(false)
function removeChannel() {
  detailsOpen.value = false
  deleteOpen.value = true
}
async function confirmRemove() {
  const topic = props.topic
  deleting.value = true
  const done = await store.removeChannel(topic.id)
  deleting.value = false
  if (!done) return
  deleteOpen.value = false
  const here = nav?.router.currentRoute.value
  if (here?.name === 'workspace-topic' && routeIds(here.params).topicId === topic.id)
    void nav?.router.replace({ name: 'workspace-project', params: { projectId: topic.project_id } })
}
useCommands(roomCommands)
</script>

<template>
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
        <span v-if="!connected" class="topic-header__disconnected" role="status">{{
          t('work.room.header.disconnected')
        }}</span>
      </span>
    </div>

    <!-- 群聊感 (fusion-design §3): the roster, as a normal child of this row.
         芝士也在这份名册里（带 Agent 标），换 AI 队友就在它那一行上。 -->
    <TopicMembers
      v-model:open="membersOpen"
      :topic-id="topic.id"
      :can-manage="topic.can_manage === true"
      :can-invite="bringsPeopleIn"
      :joined="topic.joined === true"
      :general="!isWorkTopic"
      :project-id="topic.project_id"
      :project-members="members"
      :me="me"
      @manager="managerName = $event"
    />

    <ChannelDetailsDialog
      v-if="detailsOpen"
      v-model="detailsOpen"
      :topic="topic"
      :can-manage="topic.can_manage === true"
      :manages-project="managesProject"
      :manager-name="managerName"
      :level="store.levelOf(topic.id)"
      :muted-until="store.mutedUntil(topic.id)"
      :roles="channelRoles"
      @rename="(next) => emit('rename', next)"
      @describe="(text) => store.describe(topic.id, text)"
      @set-private="(membersOnly) => store.setMembersOnly(topic.id, membersOnly)"
      @archive="archiveFromDetails"
      @unarchive="store.unarchive(topic.id)"
      @leave="leaveFromDetails"
      @delete="removeChannel"
      @open-members="openMembersFromDetails"
      @notify="(level, until) => store.setNotifyLevel(topic.id, level, until)"
      @speakers="(roles, open) => store.setSpeakers(topic.id, roles, open)"
    />

    <ChannelNotifyMenu
      v-if="topic.joined && topic.status !== 'archived'"
      :level="store.levelOf(topic.id)"
      :muted-until="store.mutedUntil(topic.id)"
      @set="(level, until) => store.setNotifyLevel(topic.id, level, until)"
    />

    <!-- 右侧面板的开关。手机上面板是页签里的一格，没有这颗。 -->
    <PanelToggle v-if="mdAndUp" :open="!!panelOpen" @toggle="emit('toggle-panel')" />

    <!-- 这一行常驻的只有标题、状态、成员。其余的都是偶尔才用的，按「做一件事 /
         看一个数」分成两段：专注模式、用量，编号垫在最底下。工作电脑在成员名册里。
         连接状态不在这里：连着是常态不用说，断了页头上自己会写「未连接」。 -->
    <AdaptiveMenu v-model="menuOpen" :actions="roomActions" :title="t('work.room.menu.more')">
      <template #activator="{ props: menuProps }">
        <BaseButton
          v-bind="menuProps"
          icon="mdi-dots-horizontal"
          size="sm"
          class="tap-target"
          :title="t('work.room.menu.more')"
          :aria-label="t('work.room.menu.more')"
          @mouseenter="loadUsage"
          @focus="loadUsage"
        />
      </template>
      <!-- 用量排在操作前面：它是这一段的口径，不是一件能做的事。桌面那颗 ⋯ 本来就
           只开它，现在它退成菜单顶上的一段；手机上这一块从底部升起（设计系统 §10.4）。 -->
      <template #desktopHeader>
        <v-card min-width="300" class="room-menu">
          <TopicUsageSummary
            class="room-menu__usage"
            :loading="usageLoading"
            :topic-usage="topicUsage"
            :project-usage="projectUsage"
            :short-id="isWorkTopic ? shortId : null"
            :topic-id="topic.id"
          />
        </v-card>
      </template>
      <template #header>
        <TopicUsageSummary
          :loading="usageLoading"
          :topic-usage="topicUsage"
          :project-usage="projectUsage"
          :short-id="isWorkTopic ? shortId : null"
          :topic-id="topic.id"
        />
      </template>
    </AdaptiveMenu>
    <!-- 改名、删除两张表单两端共用一份，不能待在手机那一支里：桌面版也得够得着。 -->
    <AdaptiveDialog
      v-model="renaming"
      :title="t('work.room.menu.renameTitle')"
      :primary-label="t('global.save')"
      :primary-disabled="!draftTitle.trim()"
      @primary="saveRename"
    >
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the person opened this dialog to type the name; focus moves into the one field it holds -->
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
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
    </AdaptiveDialog>
    <DeleteChannelDialog v-model="deleteOpen" :name="title" :busy="deleting" @confirm="confirmRemove" />
  </div>
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
.room-menu__usage {
  margin-top: 4px;
  border-top: 1px solid var(--line);
}
</style>
