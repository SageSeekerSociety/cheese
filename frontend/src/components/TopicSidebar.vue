<script setup lang="ts">
// 项目侧栏：项目名那一行 + 这个项目的话题目录（置顶的那几页、话题树、已归档）。
//
// 这个文件是**外壳**：它认路由、认项目、认对话框，把算好的东西递给
// `components/topic-sidebar/*` 那几个只管画的组件。数据那一半在
// `composables/useTopicRail.ts`（树、分组、折叠、未读、红灯的钟），路由那一半在
// `composables/useTopicRailRoutes.ts`（我在哪、点一下去哪儿、行的 ⋯ 里有哪几项）——
// 分这两半是为了让组件不认识 `vue-router`（.claude/rules/architecture.md），也让
// 折叠记不记得住、红灯会不会自己亮这些事能离开「画」单独测。
import type { Project, RoomTask, Topic } from '../cx_types'
import type { TopicUnread } from '../types/channels'
import type { MenuAction } from './common/menuAction'
import type { VirtualListHandle } from './common/VirtualList.vue'

import { computed, ref, watch } from 'vue'

import { useLongPress } from '@/composables/useLongPress'
import { useTopicRail } from '@/composables/useTopicRail'
import { useTopicRailRoutes } from '@/composables/useTopicRailRoutes'

import { DEFAULT_SHELL, projectPageLayout, shellFor, termParams } from '../lib/shell'
import { topicTitle } from '../lib/topicState'
import { normalizeTopicTitle } from '../lib/topicTitle'
import { countLabel } from '../lib/topicTree'
import { myHandle } from '../me'

import BaseButton from './base/BaseButton.vue'
import LoadingSkeleton from './common/LoadingSkeleton.vue'
import MobileActionSheet from './common/MobileActionSheet.vue'
import SecondaryNavigation from './common/Navigation/SecondaryNavigation.vue'
import UserAvatar from './common/UserAvatar.vue'
import VirtualList from './common/VirtualList.vue'
import TopicRailAllTasksRow from './topic-sidebar/TopicRailAllTasksRow.vue'
import TopicRailBrowseRow from './topic-sidebar/TopicRailBrowseRow.vue'
import TopicRailHeader from './topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from './topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRootRow from './topic-sidebar/TopicRailRootRow.vue'
import TopicRailRow from './topic-sidebar/TopicRailRow.vue'
import TopicRailTaskRow from './topic-sidebar/TopicRailTaskRow.vue'
import LeaveProjectDialog from './LeaveProjectDialog.vue'
import TransferProjectDialog from './TransferProjectDialog.vue'

import { menuActionOf } from '@/commands'
import { openPalette } from '@/commands/palette/state'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { t } from '@/i18n'

const props = defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  topics: Topic[]
  selectedTopicId: string | null
  loadingTopics: boolean
  /** 话题清单没读到时服务端给的原因；有值就地显示失败 + 重试，不画骨架。 */
  error?: string | null
  // Which 项目文档 is open in the main area ('charter'|'weeklies'|'memory'),
  // or null when none — the rail shows ONE 项目文档 row, active for
  // any of them, because which document is open is the page's business now.
  activeDocs?: string | null
  // 频道和任务在等我的东西（数字已按我的通知档位算过）；缺键 = 没有。
  unreadMap?: Record<string, TopicUnread>
  /** 这间房我静音了没有：行上画一个静音标记。 */
  mutedOf?: (topicId: string) => boolean
  // 私聊未读: {peerHandle: count}, `cheese` = 和芝士那一间。侧栏只用它的**总数**，
  // 挂在「成员」那一行上；是谁找你在成员页里说（每个人的私聊按钮上各带各的）。
  // 和 unreadMap 分开是因为私聊是按对方 handle 编址的，没有话题 id。
  privateUnreadMap?: Record<string, number>
  // 整页形态: 手机上话题列表是页面栈的一层，占满内容区，不是侧边抽屉。
  page?: boolean
  // 两栏（平板）: 还是整页形态的那份列表，但它是左边一栏、顶栏只盖着右边的房间，
  // 所以项目名那一行留在这一栏自己的顶上，不填进顶栏。
  column?: boolean
  /** 每个房间里还开着的任务（房间 id → 任务），挂在房间那一行下面。 */
  roomTasks?: Record<string, Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'presentation'>[]>
  // 每个频道里一共还有几条任务在进行；侧栏只列其中和我有关的几条（`roomTasks`）。
  roomTaskTotals?: Record<string, number>
  // 正在看哪个频道的「全部任务」。
  allTasksChannelId?: string | null
  /** 正打开的任务。 */
  selectedTaskId?: string | null
  /** 「浏览频道」那一页正开着。 */
  browsingChannels?: boolean
  /** 我能新建频道（外部成员不能）。外面说，因为「我是谁」不是这一层的事。 */
  canCreate?: boolean
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  (e: 'select-task', task: { roomId: string; taskId: string }): void
  (e: 'all-tasks', channelId: string): void
  // 「浏览频道」：去看这个项目的全部频道（侧栏只列加入了的）。
  (e: 'browse-channels'): void
  // 「新建频道」：就在这一层弹对话框（频道标题右边那颗 ＋，空项目里的主按钮）。
  // 对话框和它要发的请求都在外面：这一层不认识 store。
  (e: 'new-channel'): void
  // 指针停在一行上：让父组件（拥有这一行的路由的那个）顺手把它预热了。点这一行
  // 会发生什么由 select-topic 的接收方决定，所以「提前准备什么」也归它。
  (e: 'hover-topic', id: string): void
  (e: 'press-topic', id: string): void
  (e: 'leave-topic'): void
  // 同上，按下去就先下代码：总览/资料库那一行和任务行（见 TopicRailPinnedRows、
  // TopicRailTaskRow 各自的 `press`）。
  (e: 'press-page', key: string): void
  (e: 'press-task', task: { roomId: string; taskId: string }): void
  // 话题清单读失败后那颗「重试」：让拥有这份数据的父级再读一次。
  (e: 'retry'): void
  // Rename a topic's title from the row's ⋯ actions. A name a person chose is
  // final: the platform stops renaming that room from then on.
  (e: 'rename-topic', payload: { id: string; title: string }): void
  // Open 项目文档 in the main area. The rail always asks for 章程 — the page
  // itself carries the tabs that reach the other two.
  (e: 'select-docs', kind: 'charter' | 'weeklies' | 'memory'): void
}>()

// ---- 数据那一半 ----
// 直接把 props 递进去：composable 里每个 computed 都在读它，而 props 本身是响应式
// 的，所以父级换一份 topics，树跟着重算。
//
// 一张表也要递进去：一组话题行如果太长了会交给虚拟列表（lib/virtualList.ts 那个门槛），
// 那些行就不在 DOM 里了——「选中了就把它带进视口」那一条只能按序号让那一组自己滚。这张
// 表是那一半的落点：组 key → 那一组露出来的手。置顶行、组头各是一个独立的 `<v-list>`，
// 不在这张表里（它们永远整列渲染）；已归档那一组在 TopicRailArchivedGroup 里自己虚拟化
// （它拿滚动容器自己算窗口，选中行也由它自己留），所以也不在这张表里。
const railLists = new Map<string, VirtualListHandle>()
function setRailList(key: string, handle: unknown) {
  if (handle) railLists.set(key, handle as VirtualListHandle)
  else railLists.delete(key)
}

const {
  rootTopic,
  activeTree,
  railSections,
  toggleCollapse,
  unreadOf,
  freshOf,
  privateUnreadTotal,
  stalledOf,
  memberMarks,
  toggleTitle,
  topicById,
} = useTopicRail(props, { scrollToIndex: (key, index) => railLists.get(key)?.scrollToIndex(index) })

// ---- 路由那一半 ----
// 这个文件里一行 vue-router 都没有：换页、预热、行菜单那几项都从这一道取。
// 草稿和输入框归那一行自己（`TopicRailRow`），这里只记「哪一行在改」——一次只有
// 一行在改，而草稿放在这一层的话，改两行时上一次的草稿会漏到下一行。
const renamingTopicId = ref<string | null>(null)

const { routeName, openPage, prefetchPage, cancelPrefetch, openProject, actionsFor } = useTopicRailRoutes(
  () => props.selectedProjectId,
  { rename: (topic) => (renamingTopicId.value = topic.id) }
)

// 项目级页面：总览和资料库摆在项目名下那一行，其余几页（全部任务在内）、项目文档、
// 项目设置、转让或退出都在点项目名弹出的菜单里。它们和这个侧栏里的其他一切一样，只换内容区。
//
// 「转让项目」两处都有（菜单里一条，成员页那颗按钮保留）——它只需要「我是不是所有者
// 或这个项目的团队管理员」，项目行自己就带着这个答案。
//
// 这张表是**这一版前端认得**的项目页：key → 它长什么样。菜单里的顺序由这个项目的壳
// 说（catalog.py）；那一行摆什么由 `PROJECT_BAR_PAGES` 说，壳改不了。
//
// 文案走词表：壳把「项目」叫「工作」的时候，「{project}文档」跟着变成「工作文档」。
// 表里存的是 i18n key 而不是字面量，正因为壳能换词而组件不能。
const PROJECT_PAGES: Record<string, { label: string; icon: string }> = {
  // 总览是首页，项目名下那一行的第一格。
  'workspace-overview': { label: 'navigation.project.overview', icon: 'mdi-view-dashboard-outline' },
  'project-tasks': { label: 'navigation.project.tasks', icon: 'mdi-format-list-checks' },
  // 资料库和 @ 菜单里那一格用同一个图标：点开的是同一批文件。
  'project-library': { label: 'navigation.project.library', icon: 'mdi-folder-outline' },
  'project-members': { label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
  'project-routines': { label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' },
  'project-skills': { label: 'navigation.project.skills', icon: 'mdi-book-cog-outline' },
}
const KNOWN_PROJECT_PAGES = Object.keys(PROJECT_PAGES)

// 这个项目生效的壳。壳跟着项目行走（服务端解析好随 ProjectOut 下来），侧栏手上
// 就有那份清单，所以不额外问一次。
const shell = computed(() => shellFor(props.projects, props.selectedProjectId) ?? DEFAULT_SHELL)
const terms = computed(() => termParams(shell.value))

// 项目名下那一行只有总览和资料库，其余都进点项目名弹出的菜单（`projectPageLayout`）。
// 那一行**不再加东西**，见 .claude/rules/project-sidebar.md。
const layout = computed(() => projectPageLayout(shell.value, KNOWN_PROJECT_PAGES))

// 表里没有的 key 落空：壳比前端新时菜单里会多出一格这一版还不认识的页，那也不该
// 让侧栏白屏。
function pageOf(key: string): { label: string; icon: string } {
  return PROJECT_PAGES[key] ?? { label: key, icon: 'mdi-dots-horizontal' }
}

const barPages = computed(() => layout.value.bar.map((key) => ({ key, ...pageOf(key) })))
const menuPages = computed(() => layout.value.menu.map((key) => ({ key, ...pageOf(key) })))

function openProjectPage(name: string) {
  if (!props.selectedProjectId) return
  openPage(name)
}
// 谁负责 push，谁负责预热：指针停住的时候把这个页面的代码先下下来，等真按下去时
// 只剩下拉数据那一段。
function hoverProjectPage(name: string) {
  prefetchPage(name)
}

// 「转让项目」在这个菜单里也有一条（成员页那颗按钮保留，别删）。谁转得动，项目行
// 自己就说得出：所有者，或者管得了这个项目的团队管理员（`can_manage_members`）——
// 和成员页那颗按钮同一个判据，后端动手时按同一条规则再判一次。
//
// 「退出项目」这里也有一条（成员页那颗按钮保留，别删）——所有者换「转让项目」，其余
// 的人换「退出项目」，两句是同一件事的两半。
//
// 判据只有「我不是所有者」这一条，项目行上读得出来。为什么够：退项目退的是项目成员
// 身份，而因团队而在这里的人现在也能退（退的是这个项目，不是小队），剩下能拦的只有
// owner 那一条，而 owner 看到的是「转让项目」。
const transferOpen = ref(false)
const leaveOpen = ref(false)
const currentProject = computed(() => props.projects.find((p) => p.id === props.selectedProjectId) ?? null)
const canTransfer = computed(
  () =>
    !!currentProject.value &&
    (currentProject.value.owner_handle === myHandle() || currentProject.value.can_manage_members === true)
)
const canLeave = computed(() => !!currentProject.value && currentProject.value.owner_handle !== myHandle())
const currentProjectName = computed<string>(
  () => props.projects.find((p) => p.id === props.selectedProjectId)?.name ?? t('work.sidebar.chooseProject')
)

// 手机上的项目菜单（整页形态）：手机上项目名下不摆那一行，总览、资料库也在这张面板
// 里；接着是桌面菜单里那几项（项目文档、其余几页、项目设置、转让或退出），顺序照桌面。
const projectSheetOpen = ref(false)
const projectSheetActions = computed<MenuAction[]>(() => {
  if (!props.selectedProjectId) return []
  const page = (key: string): MenuAction => ({
    key,
    label: t(pageOf(key).label, terms.value),
    icon: pageOf(key).icon,
    badge: key === 'project-members' && privateUnreadTotal.value > 0 ? countLabel(privateUnreadTotal.value) : undefined,
    onSelect: () => openProjectPage(key),
  })
  const actions: MenuAction[] = [
    ...barPages.value.map((p) => page(p.key)),
    {
      key: 'project-docs',
      label: t('navigation.project.docs'),
      icon: 'mdi-file-document-outline',
      onSelect: () => emit('select-docs', 'charter'),
    },
    ...menuPages.value.map((p) => page(p.key)),
    {
      key: 'project-settings',
      label: t('navigation.project.settings'),
      icon: 'mdi-cog-outline',
      onSelect: () => openProjectPage('project-settings'),
    },
  ]
  if (canTransfer.value)
    actions.push({
      key: 'transfer',
      label: t('work.members.transfer'),
      icon: 'mdi-account-arrow-right-outline',
      onSelect: () => (transferOpen.value = true),
    })
  if (canLeave.value)
    actions.push({
      key: 'leave',
      label: t('work.members.leave'),
      icon: 'mdi-exit-to-app',
      danger: true,
      onSelect: () => (leaveOpen.value = true),
    })
  return actions
})

function switchProjectFromSheet(projectId: string) {
  projectSheetOpen.value = false
  openProject(projectId)
}

// Inline rename (pattern mirrors MyDevicesView's rename-in-place): a click on
// 重命名 in the row's ⋯ menu swaps the title span for a text field; enter/blur
// commits. 名字交回来的这一处负责跟原名字比一遍：值没变就不落盘。
function commitRename(topic: Topic, draft: string) {
  const title = normalizeTopicTitle(draft, topic.title)
  renamingTopicId.value = null
  if (title) emit('rename-topic', { id: topic.id, title })
}

function cancelRename() {
  renamingTopicId.value = null
}

// 行操作收进一颗 ⋯ (C5): hover 只浮出一个入口，不再是三颗并排的按钮盖住标题
// 尾巴。菜单展开期间那一颗必须留在屏幕上——它是菜单的 activator，跟着 hover
// 一起消失的话，鼠标一移进菜单，菜单自己就塌了。哪一行开着记在这里，是因为同时
// 只可能有一行开着。
const actionsMenuFor = ref<string | null>(null)
function setActionsMenu(topicId: string, open: boolean) {
  actionsMenuFor.value = open ? topicId : null
}

// 触屏上的行操作：长按一行，从底部升起这一行的操作（重命名、归档……），相当于桌面上
// 悬停出来的那颗 ⋯。整页形态（手机）没有那颗 ⋯：它常驻在行尾会盖住未读数。桌面宽度
// 的触屏上 ⋯ 还在，长按是多给的一条路。
//
// 一个 useLongPress 挂在滚动的那一段上，按下去的是哪一行由 data-row-actions 说：
// 每一行各挂一个的话，折叠、分组、归档那几段模板都得各接一遍。
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const searchTitle = computed(() =>
  props.page
    ? t('navigation.palette.open')
    : t('global.labelWithAside', { label: t('navigation.palette.open'), aside: isMac ? '⌘K' : 'Ctrl K' })
)

const railScroll = ref<HTMLElement | null>(null)
const rowSheetOpen = ref(false)
const rowSheetTopicId = ref<string | null>(null)
const rowSheetTopic = computed(() =>
  rowSheetTopicId.value ? topicById.value.get(rowSheetTopicId.value) ?? null : null
)

useLongPress(
  railScroll,
  (event) => {
    const row = (event.target as HTMLElement | null)?.closest?.('[data-row-actions]')
    const id = row?.getAttribute('data-row-actions')
    if (!id || !topicById.value.has(id)) return
    rowSheetTopicId.value = id
    if (rowSheetActions.value.length) rowSheetOpen.value = true
  },
  // 正在改名时手指按在输入框里是在选字，不是要这一行的操作。
  { disabled: () => renamingTopicId.value !== null }
)

const rowSheetActions = computed<MenuAction[]>(() =>
  rowSheetTopic.value ? actionsFor(rowSheetTopic.value).map(menuActionOf) : []
)

// 这一列里我还没有别的频道。侧栏只列我加入的，所以「没有别的行」既可能是刚建出来的
// 项目，也可能是别的频道我一个都没加入——两种情况下「新建一个」都接得上；反过来，
// 有别的行时不该多这一块。加载中和读失败都返回 false：那两种情况下「没有行」说的是
// 数据还没到，不是没有频道。
const noOtherChannels = computed(
  () => !props.error && !props.loadingTopics && !railSections.value.some((section) => section.rows.length > 0)
)

// 项目文档 (C4): 章程 / 周报集 / 记忆 在侧栏只占一行，点开进章程；
// 三选一的切换长在 ProjectDocsView 页面里（一 kind 一址，URL 照旧会变）。所以
// 这一行在任何一种文档打开时都是选中态。
const onDocs = computed(() => !!props.activeDocs)

// ---- 交给虚拟列表的那两组要的两件小事 ----
// 行的身份（和 v-for 的 key 同义）。写成具名函数而不是模板里的箭头：模板里那个箭头
// 参数没有类型来源，strict 下会报隐式 any。
function topicRowKey(row: unknown): string {
  return (row as { topic: Topic }).topic.id
}

// 有几行即使滚出窗口也得留在 DOM 里——它们各自的锚点就在那一行本身：
//   - 选中的那一行：光标可能正停在它上面，人也正要看它；
//   - ⋯ 菜单正开着的那一行：那颗 ⋯ 是菜单的 activator，跟着窗口一起消失菜单会塌；
//   - 正在原地改名的那一行：输入框和光标都在行里。
// 一行的锚点不在窗口上，就是「这一行被摘掉、锚点也跟着没」的那一类。一个都不占就返回
// undefined（等于告诉 virtua「不用特别留谁」）。
function keepFor(section: { rows: { topic: Topic }[] }): readonly number[] | undefined {
  const keep: number[] = []
  section.rows.forEach((row, index) => {
    const id = row.topic.id
    if (id === props.selectedTopicId || id === actionsMenuFor.value || id === renamingTopicId.value) keep.push(index)
  })
  return keep.length ? keep : undefined
}
</script>

<template>
  <component
    :is="page ? 'div' : SecondaryNavigation"
    :custom-class="page ? undefined : 'topic-rail'"
    :class="page ? 'topic-rail topic-rail--page' : undefined"
  >
    <!-- 两段式: 头固定 / 下面唯一滚动。原来还有第三段（尾固定的私聊栏），它
         撤掉了：私聊的未读改挂在「成员」那一行上，而那一行在头下面的置顶组里，
         本来就不随话题列表滚。 -->
    <div class="d-flex flex-column fill-height">
      <TopicRailHeader
        :page="page === true"
        :column="column === true"
        :project-name="currentProjectName"
        :private-unread-total="privateUnreadTotal"
        :search-title="searchTitle"
        :menu-open="projectSheetOpen"
        :menu-pages="menuPages"
        :docs-active="onDocs"
        :route-name="routeName"
        :terms="terms"
        :project-selected="!!selectedProjectId"
        :can-transfer="canTransfer"
        :can-leave="canLeave"
        @open-page="openProjectPage"
        @open-palette="openPalette()"
        @open-sheet="projectSheetOpen = true"
        @select-docs="emit('select-docs', 'charter')"
        @open-transfer="transferOpen = true"
        @open-leave="leaveOpen = true"
      />

      <TransferProjectDialog v-model="transferOpen" :project-id="selectedProjectId ?? ''" />
      <LeaveProjectDialog v-model="leaveOpen" :project-id="selectedProjectId ?? ''" />
      <!-- 手机上的项目菜单。项目名下那一行（总览、资料库）在手机上也收进这里：列表只留
           频道。换项目也只能在这里——一个项目
           一格的那条竖 rail 只在桌面渲染，底栏「工作区」那一格只落到一个项目。 -->
      <MobileActionSheet v-if="page" v-model="projectSheetOpen" :actions="projectSheetActions">
        <div v-if="projects.length > 1" class="project-switch">
          <div class="project-switch__head t-eyebrow">{{ t('work.sidebar.switchProject') }}</div>
          <button
            v-for="p in projects"
            :key="p.id"
            type="button"
            class="project-switch__item"
            :aria-current="p.id === selectedProjectId ? 'true' : undefined"
            @click="switchProjectFromSheet(p.id)"
          >
            <!-- 项目头像走 UserAvatar（kind="org" = 圆角方块，§3.14）：和左栏人像同一个组件、同一套
                 「没图就退成底色首字母」的规矩，不再这里自己截首字、自己上色。种子用
                 项目 id —— 颜色跟着项目走，改名不换色（契约 §3.14）。 -->
            <UserAvatar kind="org" :name="p.name" :seed="p.id" :size="20" class="project-switch__avatar" />
            <span class="project-switch__name">{{ p.name }}</span>
            <v-icon v-if="p.id === selectedProjectId" size="18" class="project-switch__check" icon="mdi-check" />
          </button>
          <div class="project-switch__rule" />
        </div>
      </MobileActionSheet>
      <MobileActionSheet
        v-model="rowSheetOpen"
        :actions="rowSheetActions"
        :title="rowSheetTopic ? topicTitle(rowSheetTopic) : undefined"
      />

      <!-- 中段：这个侧栏里唯一会滚的东西 -->
      <div ref="railScroll" class="rail-scroll flex-grow-1 overflow-y-auto">
        <template v-if="!selectedProjectId">
          <div class="t-body c-muted pa-4">{{ t('work.sidebar.chooseProjectFirst') }}</div>
        </template>
        <template v-else>
          <!-- 列表顶上由外面填的一行（手机上是去项目总览的那一行，见 ProjectSidebar）。 -->
          <slot name="top" />
          <!-- 项目名下面一行：总览和资料库，别的不放（.claude/rules/project-sidebar.md）。 -->
          <TopicRailPinnedRows
            :pages="barPages"
            :route-name="routeName"
            :terms="terms"
            :page="page === true"
            @open-page="openProjectPage"
            @hover-page="hoverProjectPage"
            @press-page="emit('press-page', $event)"
            @cancel-prefetch="cancelPrefetch()"
          />

          <!-- 新建频道就在这一组标题右边：常驻可见、不藏进 hover，点一下就地弹对话框，
               不再跳到「浏览频道」那一页（那里只管找和加入别人的频道）。位置钉在这一
               行上，频道再多也不动。 -->
          <div class="t-eyebrow side-subhead">
            <span class="side-subhead__label">{{ t('work.sidebar.topics') }}</span>
            <button
              v-if="canCreate"
              type="button"
              class="side-subhead__add tap-target"
              data-testid="new-channel-entry"
              :title="t('work.projectSettings.channels.create')"
              :aria-label="t('work.projectSettings.channels.create')"
              @click="emit('new-channel')"
            >
              <v-icon size="16" icon="mdi-plus" />
            </button>
          </div>

          <!-- 频道的第一行：项目自带的「综合」，固定在最上面，和其他频道同一组。 -->
          <TopicRailRootRow
            v-if="!error"
            :root-topic="rootTopic"
            :selected-topic-id="selectedTopicId"
            :page="page === true"
            :unread-of="unreadOf"
            :fresh-of="freshOf"
            :muted-of="mutedOf"
            :root-actions="rootTopic ? actionsFor(rootTopic).map(menuActionOf) : []"
            @select-topic="emit('select-topic', $event)"
            @hover-topic="emit('hover-topic', $event)"
            @press-topic="emit('press-topic', $event)"
            @leave-topic="emit('leave-topic')"
          >
            <template #root-tasks>
              <TopicRailTaskRow
                v-for="task in rootTopic ? roomTasks?.[rootTopic.id] ?? [] : []"
                :key="task.id"
                :task="task"
                :selected="task.id === selectedTaskId"
                :unread="unreadOf(task.id)"
                @select="emit('select-task', $event)"
                @press="emit('press-task', $event)"
              />
              <TopicRailAllTasksRow
                v-if="rootTopic && roomTaskTotals?.[rootTopic.id]"
                :channel-id="rootTopic.id"
                :total="roomTaskTotals[rootTopic.id]"
                :label="t('work.sidebar.allTasks')"
                :selected="allTasksChannelId === rootTopic.id"
                @select="emit('all-tasks', $event)"
              />
            </template>
          </TopicRailRootRow>

          <!-- Topic list failed to load: replace this block in place with an error
               and a retry (docs/design-system.md §3.10), not a toast that is gone in
               seconds — once it is, this block looks exactly like "no topics" and
               you cannot tell broken from empty. -->
          <BaseLoadError
            v-if="error"
            :title="t('shell.workspaceErrors.loadTopics')"
            :error="error"
            class="rail-error"
            @retry="emit('retry')"
          />

          <LoadingSkeleton v-else-if="loadingTopics" variant="list" class="rail-skel" />

          <template v-else>
            <!-- 一个别的频道都还没有：光一个 ＋ 太安静，把话说出来，并给一颗主按钮
                顶上（空项目里这是唯一要做的事）。 -->
            <div v-if="canCreate && noOtherChannels" class="rail-empty" data-testid="no-other-channels">
              <span class="t-meta c-faint">{{ t('work.sidebar.noOtherChannels') }}</span>
              <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" @click="emit('new-channel')">
                {{ t('work.projectSettings.channels.create') }}
              </BaseButton>
            </div>

            <template v-for="section in railSections" :key="section.key">
              <v-list v-if="section.rows.length" density="compact" nav class="py-0" tabindex="-1">
                <!-- Rows are ordered by most recent activity, so a new message pushes a room
                     to the top. Below the threshold the whole column is in the DOM and
                     TransitionGroup slides the row there (FLIP) instead of snapping the
                     list: where the row was and where it went has to stay visible. Past
                     VIRTUAL_LIST_THRESHOLD (lib/virtualList.ts) the column goes to
                     VirtualList — those rows could never fit on screen together anyway, so
                     the saving is nodes, not behaviour. Keyed by project: switching projects
                     swaps the whole list, it is not this list reordering, so nothing plays.

                     No `shift`: virtua's `shift` only acts when the row COUNT changes, and it
                     then anchors the view to the tail (= assumes the change was at the head).
                     Measured in headless chromium (150 rows, viewing r100-r108): moving a
                     middle row to the head changes nothing (same count), a new row at the head
                     holds the view with shift and slides it by one row without — but dropping
                     5 rows BELOW the view (archiving, collapsing a subtree, the hidden-stalled
                     filter — all of which happen away from the head here) yanks the view up 5
                     rows with shift and leaves it alone without. The rail's count changes are
                     mostly not at the head, so the anchor is left at the start. -->
                <VirtualList
                  :ref="(handle: unknown) => setRailList(section.key, handle)"
                  :items="section.rows"
                  :item-key="topicRowKey"
                  :scroll-parent="railScroll"
                  :estimated-size="36"
                  :buffer-size="320"
                  :keep-mounted="keepFor(section)"
                  transition="rail-row"
                  :transition-key="selectedProjectId ?? undefined"
                >
                  <template #item="{ item }">
                    <div>
                      <TopicRailRow
                        :row="item"
                        :selected="item.topic.id === selectedTopicId"
                        :page="page === true"
                        :renaming="renamingTopicId === item.topic.id"
                        :menu-open="actionsMenuFor === item.topic.id"
                        :stalled="stalledOf(item.topic.id)"
                        :muted="mutedOf?.(item.topic.id) ?? false"
                        :fresh="freshOf(item.topic.id)"
                        :marks="memberMarks(item.topic)"
                        :toggle-title="toggleTitle(item)"
                        :actions="actionsFor"
                        @select="emit('select-topic', $event)"
                        @hover="emit('hover-topic', $event)"
                        @press="emit('press-topic', $event)"
                        @leave="emit('leave-topic')"
                        @toggle-collapse="toggleCollapse"
                        @commit-rename="(draft: string) => commitRename(item.topic, draft)"
                        @cancel-rename="cancelRename()"
                        @update:menu-open="(open: boolean) => setActionsMenu(item.topic.id, open)"
                      />
                      <TopicRailTaskRow
                        v-for="task in roomTasks?.[item.topic.id] ?? []"
                        :key="task.id"
                        :task="task"
                        :depth="item.depth"
                        :selected="task.id === selectedTaskId"
                        :unread="unreadOf(task.id)"
                        @select="emit('select-task', $event)"
                        @press="emit('press-task', $event)"
                      />
                      <TopicRailAllTasksRow
                        v-if="roomTaskTotals?.[item.topic.id]"
                        :channel-id="item.topic.id"
                        :total="roomTaskTotals[item.topic.id]"
                        :depth="item.depth"
                        :label="t('work.sidebar.allTasks')"
                        :selected="allTasksChannelId === item.topic.id"
                        @select="emit('all-tasks', $event)"
                      />
                    </div>
                  </template>
                </VirtualList>
              </v-list>
            </template>

            <TopicRailBrowseRow
              :label="t('work.channel.browse')"
              :selected="browsingChannels === true"
              @select="emit('browse-channels')"
            />
          </template>
        </template>
      </div>

      <!-- 新建项目 moved to the project rail's + (App.vue) — one affordance,
           Discord-style. The create-project emit stays for API compatibility. -->
    </div>
  </component>
</template>

<style scoped>
/* 整页形态（手机上的话题列表）：占满内容区，不画抽屉那条右边线。这时它是内容，
   不是侧栏，底色跟着内容区走（design-system §1.4）。 */
.topic-rail--page {
  width: 100%;
  height: 100%;
}
.side-subhead {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px 4px;
}
.side-subhead__label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 「频道」右边那颗 ＋：常驻可见但不抢眼（--faint 的 16px 图标），hover 才亮成琥珀。
   负的外边距是把它收进标题行的内距里——多一颗按钮，这一行的高度不变。
   .tap-target 要一个定位的锚点，所以 position: relative。 */
.side-subhead__add {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin: -4px -6px -4px 0;
  color: var(--faint);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  transition:
    color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
.side-subhead__add:hover {
  color: var(--accent-ink);
  background: var(--fill);
}

/* 还没有别的频道时那一块：一句话 + 主按钮。 */
.rail-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 16px 4px;
}

/* 话题还在路上时，先把行的形状画出来（LoadingSkeleton）。这条 rail 的底是
   --canvas，骨架默认那档 --fill-2 压上去只有 1.083:1，等于什么都没画；--line-2 是
   这条 rail 上「再离底一档」的那个值（选中行用的也是它），在两个主题下都看得见。 */
.rail-skel {
  --skel-bone: var(--line-2);
}

/* 就地报错和上面那一列话题对齐（subhead 的内距是 16px），右边留出一点收口。 */
.rail-error {
  padding: 8px 16px 4px;
}

/* 行换位置、进出（见模板里 TransitionGroup 那段）。走掉的那一行脱离文档流，否则
   下面几行要等它淡完才补位，那是一次跳；左右 8px 是 `.v-list--nav` 自己的内边距。
   减弱动效时全局那条把时长压到 0.001ms，进出瞬间完成，不用在这儿单独关。 */
.rail-row-move {
  transition: transform var(--dur-base) var(--ease-standard);
}
.rail-row-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.rail-row-leave-active {
  position: absolute;
  left: 8px;
  right: 8px;
  transition: opacity var(--dur-quick) var(--ease-in);
}
.rail-row-enter-from,
.rail-row-leave-to {
  opacity: 0;
}

/* 手机项目菜单里的「切换项目」：一行的尺寸、字号和面板里的操作行一样（手指点得中），
   头像换成项目自己的方头像。当前这个项目行尾一个勾，不用琥珀——它不是导航位置。 */
.project-switch__head {
  padding: 4px 20px;
}
.project-switch__item {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-height: 48px;
  padding: 0 20px;
  color: var(--text);
  font-size: 15px;
  line-height: var(--lh-15);
  text-align: start;
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.project-switch__item:active {
  background: var(--fill);
}
.project-switch__avatar {
  flex: none;
}
.project-switch__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-switch__check {
  flex: none;
  color: var(--muted);
}
.project-switch__rule {
  height: 1px;
  margin: 4px 0;
  background: var(--line);
}
/* 整页形态：手指点的地方至少 44px 高。行和组头那两个组件自己也各写了一条（它们
   要能单独渲染），这里这条管的是这一层画不出来的部分。 */
.topic-rail--page .group-toggle {
  min-height: 44px;
}
/* 整页形态：＋ 画大一点（32px），手指点得中——命中区由 .tap-target 撑到 44px。 */
.topic-rail--page .side-subhead__add {
  width: 32px;
  height: 32px;
  margin-right: -8px;
}
</style>
