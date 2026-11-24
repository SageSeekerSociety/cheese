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
// 频道本身能做的事只有一份（commands/topicActions.ts）：侧栏右键、命令面板列的就是它。
// 桌面页头上不再放管频道的 ⋯ 和铃铛：点频道名、点那一串头像打开频道详情（关于 / 成员 /
// 设置），静音时频道名旁边一个记号。手机上页头是顶栏，那一份清单留在顶栏的 ⋯ 里。
import type { ChannelDetailsTab } from '@/commands/channelDetails'
import type { MenuAction } from '@/components/common/menuAction'
import type { TaskBlockButton } from '@/components/task/taskBlocks'
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { useQuery } from '@tanstack/vue-query'

import { useNavigation } from '@/composables/useNavigation'
import { useTopicUsage } from '@/composables/useTopicUsage'

import { menuActionOf, useCommands } from '@/commands'
import { channelDetailsRequest } from '@/commands/channelDetails'
import { copyLink, linkOf } from '@/commands/copy'
import { notifyLevelName } from '@/commands/notifyChoices'
import { archiveTopic, topicActions } from '@/commands/topicActions'
import BaseButton from '@/components/base/BaseButton.vue'
import ChannelDetailsDialog from '@/components/channel/ChannelDetailsDialog.vue'
import DeleteChannelDialog from '@/components/channel/DeleteChannelDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import PageSidebarToggle from '@/components/common/Navigation/PageSidebarToggle.vue'
import TopicMembers from '@/components/TopicMembers.vue'
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
  /**
   * 对话旁边那几块（概览、支线、文件，例行事务在 ⋯ 里）：页头上一样一颗按钮，带着它的名字、图标、
   * 开没开着，和支线上有没读过的回复时的点。
   */
  blocks?: TaskBlockButton[]
  /** 读者挪过这一页的摆法时给：那一组的 ⋯ 里（手机上是顶栏的 ⋯ 里）多一项「恢复默认摆法」。 */
  resetLayout?: () => void
  /** 那几颗按钮开合的那几块的 id。 */
  panelControls?: string
}>()

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'rename', title: string): void
  /** 点了其中一颗：它那一块开着就关掉，关着就打开。 */
  (e: 'toggle-block', key: string): void
  // 这个话题换了 AI 队友。对话栏要重拉名册——它显示的 AI 名字来自那份名册。
}>()

const { mdAndUp } = useDisplay()
const blockButtons = computed(() => (props.blocks ?? []).filter((block) => !block.more))
const moreBlocks = computed(() => (props.blocks ?? []).filter((block) => block.more))
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
const store = useWorkspaceStore()
// 静音时频道名旁边那个记号：不能点，悬停说的是此刻那一档。
const muted = computed(() => props.topic.joined === true && store.levelOf(props.topic.id) === 'mute')
const mutedLabel = computed(() => notifyLevelName('mute', store.mutedUntil(props.topic.id)))

// 用量在频道详情的「关于」里：打开详情时取（取哪两份、什么时候算新鲜在
// `composables/useTopicUsage.ts` 那一层）。
const {
  loading: usageLoading,
  topic: topicUsage,
  project: projectUsage,
  load: loadUsage,
} = useTopicUsage({ topicId: () => props.topic.id, projectId: () => props.topic.project_id })

const nav = useNavigation()
const menuOpen = ref(false)
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

// 同一份频道操作：手机顶栏的 ⋯ 里是一行一行的菜单，命令面板里是「操作」。
// `topicActions` 要的是整台 router（复制链接靠它解析、新建任务靠它跳），从 `nav.router`
// 上取；宿主没装路由时没有去处，这份菜单就空着——和别处「没有路由就少画一点」同一个道理。
function roomCommands() {
  if (!nav) return []
  return topicActions(props.topic, nav.router, { rename: startRename, details: () => openDetails('about') })
}
// 「恢复默认摆法」：桌面上在那一组的 ⋯ 里，手机上跟在顶栏 ⋯ 的最后；命令面板里那一条是
// 页面登记的（views/workspace/roomLayout.ts）。
const resetCommand = computed<MenuAction | null>(() =>
  props.resetLayout
    ? menuActionOf({
        id: 'room.resetLayout',
        title: t('shell.layout.reset'),
        icon: 'mdi-restore',
        run: props.resetLayout,
        divider: true,
      })
    : null
)
const roomActions = computed<MenuAction[]>(() => [
  ...roomCommands().map(menuActionOf),
  ...(resetCommand.value ? [resetCommand.value] : []),
])

// 频道详情：点页头的频道名、那一串头像、菜单里的「频道详情」打开。管理者是谁从那一串
// 头像那里来（名册）。
const detailsOpen = ref(false)
const detailsTab = ref<ChannelDetailsTab>('about')
function openDetails(tab: ChannelDetailsTab) {
  detailsTab.value = tab
  detailsOpen.value = true
}
watch(detailsOpen, (open) => {
  if (open) void loadUsage()
})
// 别处（侧栏右键、命令面板）要开这个频道的详情：先把人带到这里，再由这里打开。
watch(
  [channelDetailsRequest, () => props.topic.id],
  ([request, id]) => {
    if (!request || request.topicId !== id) return
    channelDetailsRequest.value = null
    openDetails(request.tab)
  },
  { immediate: true }
)
const managerName = ref<string | null>(null)
// 我在这个项目里能做什么：详情打开时才问，和工作区别处读同一份项目（`query/project`）。
const projectRead = useQuery(
  computed(() => ({
    ...projectQuery(props.topic.project_id),
    enabled: detailsOpen.value,
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
function copyChannelLink() {
  if (!nav) return
  void copyLink(
    linkOf(nav.router, {
      name: 'workspace-topic',
      params: { projectId: props.topic.project_id, topicId: props.topic.id },
    })
  )
}

// 删除频道：详情「设置」开到这张确认框上，真正删的是 store（它删完把侧栏那一行去掉）。删成了就走——内容是刚被删掉的那个频道，留着
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
    <PageSidebarToggle v-if="mdAndUp" />
    <!-- 桌面标题和状态沿同一基线排列，编号放在详情里。 -->
    <div class="topic-header__text">
      <button
        type="button"
        class="topic-header__title topic-header__title--button t-title"
        :title="title"
        :aria-label="t('work.channelDetails.open', { name: title })"
        @click="openDetails('about')"
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
      <span
        v-if="muted"
        class="topic-header__muted"
        role="img"
        :title="mutedLabel"
        :aria-label="mutedLabel"
        data-testid="channel-muted"
        ><v-icon size="16" icon="mdi-bell-off-outline"
      /></span>
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

    <!-- The roster as a stack of faces; pressing it opens the details on the members page. -->
    <TopicMembers :topic-id="topic.id" @open="openDetails('members')" @manager="managerName = $event" />

    <ChannelDetailsDialog
      v-if="detailsOpen"
      v-model="detailsOpen"
      :topic="topic"
      :initial-tab="detailsTab"
      :can-manage="topic.can_manage === true"
      :manages-project="managesProject"
      :manager-name="managerName"
      :level="store.levelOf(topic.id)"
      :muted-until="store.mutedUntil(topic.id)"
      :roles="channelRoles"
      :short-id="isWorkTopic ? shortId : null"
      :usage-loading="usageLoading"
      :topic-usage="topicUsage"
      :project-usage="projectUsage"
      :project-members="members"
      :me="me"
      :can-invite="bringsPeopleIn"
      @rename="(next) => emit('rename', next)"
      @describe="(text) => store.describe(topic.id, text)"
      @set-private="(membersOnly) => store.setMembersOnly(topic.id, membersOnly)"
      @archive="archiveFromDetails"
      @unarchive="store.unarchive(topic.id)"
      @leave="leaveFromDetails"
      @delete="removeChannel"
      @copy-link="copyChannelLink"
      @notify="(level, until) => store.setNotifyLevel(topic.id, level, until)"
      @speakers="(roles, open) => store.setSpeakers(topic.id, roles, open)"
    />

    <!-- Phones: the top bar has no room for anything else, so the channel's one list of actions
         lives under this ⋯ (desktop reaches it by right-clicking the sidebar row or the palette). -->
    <AdaptiveMenu v-if="!mdAndUp" v-model="menuOpen" :actions="roomActions" :title="title">
      <template #activator="{ props: menuProps }">
        <BaseButton
          v-bind="menuProps"
          icon="mdi-dots-horizontal"
          size="sm"
          class="tap-target"
          :title="t('work.room.menu.more')"
          :aria-label="t('work.room.menu.more')"
        />
      </template>
    </AdaptiveMenu>

    <!-- The blocks that can sit beside the conversation, one button each (the less used under ⋯):
         opens its block, or closes it when it is open; several can be open at once. Last in the row,
         above the blocks it controls; what is about the channel itself (roster, notifications, the menu)
         sits before it. Phones show them as tabs instead.
         Focus returns to the first one (data-panel-toggle) when the floating panel closes. -->
    <div
      v-if="mdAndUp && blockButtons.length"
      class="topic-header__things"
      role="group"
      :aria-label="t('work.task.form.blocks')"
    >
      <span v-for="(block, i) in blockButtons" :key="block.key" class="topic-header__thing">
        <BaseButton
          kind="ghost"
          size="sm"
          class="topic-header__thing-button"
          :class="{ 'topic-header__thing-button--on': block.on }"
          :icon="block.icon"
          :data-panel-toggle="i === 0 ? '' : undefined"
          :data-panel-thing="block.key"
          :aria-pressed="block.on ? 'true' : 'false'"
          :aria-controls="panelControls"
          :aria-label="block.hint ?? block.label"
          :title="block.hint ?? block.label"
          @click="emit('toggle-block', block.key)"
        />
        <span v-if="block.dot" class="topic-header__thing-dot" aria-hidden="true" />
      </span>
      <v-menu v-if="moreBlocks.length || resetCommand" location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            size="sm"
            class="topic-header__thing-button"
            :class="{ 'topic-header__thing-button--on': moreBlocks.some((block) => block.on) }"
            icon="mdi-dots-horizontal"
            :aria-label="t('work.task.form.moreBlocks')"
            :title="t('work.task.form.moreBlocks')"
            data-testid="channel-blocks-more"
          />
        </template>
        <v-list density="compact" role="menu" :aria-label="t('work.task.form.moreBlocks')">
          <v-list-item
            v-for="block in moreBlocks"
            :key="block.key"
            role="menuitemcheckbox"
            :aria-checked="block.on ? 'true' : 'false'"
            :title="block.label"
            :prepend-icon="block.icon"
            :append-icon="block.on ? 'mdi-check' : undefined"
            :data-panel-thing="block.key"
            @click="emit('toggle-block', block.key)"
          />
          <template v-if="resetCommand">
            <v-divider v-if="moreBlocks.length" class="my-1" />
            <v-list-item
              role="menuitem"
              :title="resetCommand.label"
              :prepend-icon="resetCommand.icon"
              @click="resetCommand.onSelect?.()"
            />
          </template>
        </v-list>
      </v-menu>
    </div>
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
/* 对话旁边那几块：框在一起的一组图标按钮，开着的那几颗是按下的样子。件数不画在按钮上，在它的
   提示里。 */
.topic-header__things {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
  align-items: center;
  padding: 2px;
  margin-left: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.topic-header__thing {
  position: relative;
  display: inline-flex;
}
/* 比页头上别的图标按钮小一号：几颗框在一起，整组不该比旁边的铃铛高。 */
.topic-header__things .topic-header__thing-button {
  width: 30px;
  min-width: 0;
  height: 30px;
  --v-icon-size-multiplier: 0.85;
}
.topic-header__thing-button--on {
  color: var(--ink);
  background: var(--fill-2);
}
/* 有没看过的东西（支线上的新回复）：按钮右上角一颗琥珀点，和页签上的那颗同一个记号。 */
.topic-header__thing-dot {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 6px;
  height: 6px;
  pointer-events: none;
  background: var(--accent);
  border-radius: var(--radius-pill);
}
/* 静音的记号：不能点，和频道名挨着，比名字弱一档。 */
.topic-header__muted {
  display: inline-flex;
  flex: none;
  color: var(--muted);
}
</style>
