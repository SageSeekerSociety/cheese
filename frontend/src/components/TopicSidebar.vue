<script setup lang="ts">
// 项目侧栏：项目名那一行 + 这个项目的话题目录（置顶的那几页、话题树、已归档）。
//
// 这个文件是**外壳**：它认路由、认项目、认对话框，把算好的东西递给
// `components/topic-sidebar/*` 那几个只管画的组件。数据那一半在
// `composables/useTopicRail.ts`（树、分组、折叠、未读、红灯的钟），路由那一半在
// `composables/useTopicRailRoutes.ts`（我在哪、点一下去哪儿、行的 ⋯ 里有哪几项）——
// 分这两半是为了让组件不认识 `vue-router`（.claude/rules/architecture.md），也让
// 折叠记不记得住、红灯会不会自己亮这些事能离开「画」单独测。
import type { Project, Topic } from '../cx_types'
import type { MenuAction } from './common/menuAction'
import type { VirtualListHandle } from './common/VirtualList.vue'

import { computed, ref, watch } from 'vue'

import { useLongPress } from '@/composables/useLongPress'
import { useTopicRail } from '@/composables/useTopicRail'
import { useTopicRailRoutes } from '@/composables/useTopicRailRoutes'

import { DEFAULT_SHELL, projectPagePlan, shellFor, termParams } from '../lib/shell'
import { loadRevealedPages, withRevealedPage } from '../lib/shellPrefs'
import { topicTitle } from '../lib/topicState'
import { normalizeTopicTitle } from '../lib/topicTitle'
import { countLabel } from '../lib/topicTree'
import { myHandle } from '../me'
import { avatarColor, avatarInitial } from '../utils/avatar'

import LoadingSkeleton from './common/LoadingSkeleton.vue'
import MobileActionSheet from './common/MobileActionSheet.vue'
import SecondaryNavigation from './common/Navigation/SecondaryNavigation.vue'
import VirtualList from './common/VirtualList.vue'
import TopicRailArchivedGroup from './topic-sidebar/TopicRailArchivedGroup.vue'
import TopicRailGroupToggle from './topic-sidebar/TopicRailGroupToggle.vue'
import TopicRailHeader from './topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from './topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRow from './topic-sidebar/TopicRailRow.vue'
import LeaveProjectDialog from './LeaveProjectDialog.vue'
import TransferProjectDialog from './TransferProjectDialog.vue'

import { menuActionOf } from '@/commands'
import { openPalette } from '@/commands/palette/state'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  projects: Project[]
  selectedProjectId: string | null
  topics: Topic[]
  selectedTopicId: string | null
  loadingTopics: boolean
  creatingTopic?: boolean
  // Which 项目文档 is open in the main area ('charter'|'weeklies'|'memory'),
  // or null when none — the rail shows ONE 项目文档 row, active for
  // any of them, because which document is open is the page's business now.
  activeDocs?: string | null
  // 话题级未读 (Feishu-style): {topicId: count}; missing key = no unread.
  // 静音的房间已经被调用处去掉了（store.badgeUnreadMap）。
  unreadMap?: Record<string, number>
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
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  // 指针停在一行上：让父组件（拥有这一行的路由的那个）顺手把它预热了。点这一行
  // 会发生什么由 select-topic 的接收方决定，所以「提前准备什么」也归它。
  (e: 'hover-topic', id: string): void
  (e: 'press-topic', id: string): void
  (e: 'leave-topic'): void
  (e: 'create-topic', title: string): void
  /** 下载整个项目的存档：取数在上面的视图里做，这里只报意图。 */
  (e: 'export-project'): void
  // 已归档那一组里行尾的「取消归档」。
  (e: 'unarchive-topic', id: string): void
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
  archivedRows,
  mineTree,
  othersCount,
  railSections,
  toggleCollapse,
  toggleOthers,
  unreadOf,
  archivedUnread,
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

// 项目级页面（看板/资料库/…）住在话题列表最上面的置顶行里，和话题行同一种视觉
// 语法——它们和这个侧栏里的其他一切一样，只换内容区。项目设置不在这里：它是
// 一年点两次的东西，收进项目头的 ⋯ 菜单。
//
// 「退出项目」只在成员页：那里有名册，知道我是所有者、负责人还是团队带进来的人，
// 而这几种人能不能退各不相同。项目行上读不出这些，按它判会把退出递给退不掉的人。
// 「转让项目」两处都有（这里一条，成员页那颗按钮保留）——它只需要「我是不是所有者
// 或这个项目的团队管理员」，项目行自己就带着这个答案。
//
// 这张表是**这一版前端认得**的项目页：key → 它长什么样。露出哪几格、什么顺序、谁
// 开局收着，全部由这个项目的壳说（catalog.py）。default 壳说的是「今天」的样子：
// 侧栏那一面资料库和名册是常驻那两格，例行和技能收进项目名旁边那个 ⋯ 菜单——#1330
// 把这条竖线收窄过一轮，名册又回到侧栏（#6：「退出项目」长在名册页上，名册收进 ⋯
// 就没人找得到怎么退出），壳的 default 声明跟着一起改，否则这一版会把别人刚挪走的
// 几格又摆回来。
//
// 文案走词表：壳把「项目」叫「工作」的时候，「{project}文档」跟着变成「工作文档」。
// 表里存的是 i18n key 而不是字面量，正因为壳能换词而组件不能。
const PROJECT_PAGES: Record<string, { label: string; icon: string }> = {
  // 看板就是首页（项目名那一行点下去就到），但它仍然是一页：壳想把它摆回侧栏也行。
  'workspace-running': { label: 'navigation.project.board', icon: 'mdi-view-column-outline' },
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

// 「个人级压过壳」：他手动打开过一次的收起页，之后就在他自己的侧栏里。按 handle
// 存——这是**这个人**对某一个壳的选择，和 projectOrder 同一个理由。
const revealed = ref<ReadonlySet<string>>(new Set<string>())
watch(
  () => myHandle(),
  (handle) => {
    revealed.value = loadRevealedPages(handle)
  },
  { immediate: true }
)

const plan = computed(() => projectPagePlan(shell.value, KNOWN_PROJECT_PAGES, revealed.value))

// 表里没有的 key 落空：壳比前端新时菜单里会多出一格这一版还不认识的页，那也不该
// 让侧栏白屏。
function pageOf(key: string): { label: string; icon: string } {
  return PROJECT_PAGES[key] ?? { label: key, icon: 'mdi-dots-horizontal' }
}

// 「一年点几次」的那几页住在项目名旁边那个 ⋯ 菜单里（#1330）：仍然一次点击可达，
// 只是不再占着每天都要扫一遍的那条竖线。谁在菜单里由壳说——**侧栏上没摆出来的
// 全部**都在这里，包括壳写错了 key、或这一版前端还不认识的页，所以它们不会凭空
// 消失（画的时候 key 不认识就落成那一个字面量，见 pageOf）。文案和侧栏同一条来源，
// 理由也一样：壳能换词。
// 首页不进菜单：项目名那一行就是它的入口，同一个地方两个入口只会让人猜哪个才算数。
const homePage = computed(() => shell.value.home ?? 'workspace-running')
const onHome = computed(() => routeName.value === homePage.value)
const menuPages = computed(() =>
  plan.value.more.filter((key) => key !== homePage.value).map((key) => ({ key, ...pageOf(key) }))
)
// 侧栏上摆出来的那几页（顺序就是壳说的顺序），置顶行按它画。
const visiblePages = computed(() => plan.value.visible.map((key) => ({ key, ...pageOf(key) })))

function openProjectPage(name: string) {
  if (!props.selectedProjectId) return
  // 打开一个默认收起的页 = 这一页对他有用。记住它，下次它在外面。
  // 首页不算：它的入口是项目名那一行，记成「打开过」会把它摆回侧栏，成了第二个入口。
  if (name !== homePage.value && plan.value.more.includes(name)) {
    revealed.value = withRevealedPage(revealed.value, name, myHandle())
  }
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

// 手机上的项目菜单（整页形态）：侧栏上摆在话题上面的那几页、项目文档、平时收在 ⋯
// 里的那几页、项目设置、转让或退出，一张面板全列出来。顺序照桌面：先是侧栏上那几
// 行，再是菜单里那几项。
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
    ...plan.value.visible.map(page),
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
    {
      key: 'export',
      label: t('navigation.project.export'),
      icon: 'mdi-download-outline',
      onSelect: () => emit('export-project'),
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

// New topic: don't ask the human for a title — create an untitled one and open
// it; the title is derived from the first message (and 芝士 can refine it).
//
// 房间是个群聊，不「交给」谁：建出来时坐着项目的默认队友，别的队友和人一样从
// 成员名册请进来。
function newTopic() {
  emit('create-topic', '')
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
        :home-active="onHome"
        :home-key="homePage"
        :home-icon="pageOf(homePage).icon"
        :project-name="currentProjectName"
        :private-unread-total="privateUnreadTotal"
        :search-title="searchTitle"
        :menu-open="projectSheetOpen"
        :menu-pages="menuPages"
        :route-name="routeName"
        :terms="terms"
        :project-selected="!!selectedProjectId"
        :can-transfer="canTransfer"
        :can-leave="canLeave"
        @open-page="openProjectPage"
        @open-palette="openPalette()"
        @open-sheet="projectSheetOpen = true"
        @open-transfer="transferOpen = true"
        @open-leave="leaveOpen = true"
        @export="emit('export-project')"
      />

      <TransferProjectDialog v-model="transferOpen" :project-id="selectedProjectId ?? ''" />
      <LeaveProjectDialog v-model="leaveOpen" :project-id="selectedProjectId ?? ''" />
      <!-- 手机上的项目菜单。话题列表上面那几行（资料库、成员、项目文档）在手机上收进
           这里：列表只留话题，打开项目先看到的是它们。换项目也只能在这里——一个项目
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
            <span
              class="dm-avatar project-avatar project-switch__avatar"
              :style="{ backgroundColor: avatarColor(p.name) }"
              >{{ avatarInitial(p.name) }}</span
            >
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
          <!-- 列表顶上由外面填的一行（手机上是看板的摘要，见 ProjectSidebar）。 -->
          <slot name="top" />
          <!-- 置顶行 (C1): 全局房间 + 这个项目露出来的那几页 + 项目文档。和话题行同一
               种语法——同图标槽、同缩进基准、同选中态、同未读角标，所以「点它会发生
               什么」不用另学一遍。 -->
          <TopicRailPinnedRows
            :root-topic="rootTopic"
            :selected-topic-id="selectedTopicId"
            :pages="visiblePages"
            :route-name="routeName"
            :terms="terms"
            :docs-active="onDocs"
            :private-unread-total="privateUnreadTotal"
            :page="page === true"
            :unread-of="unreadOf"
            :muted-of="mutedOf"
            @select-topic="emit('select-topic', $event)"
            @hover-topic="emit('hover-topic', $event)"
            @press-topic="emit('press-topic', $event)"
            @leave-topic="emit('leave-topic')"
            @open-page="openProjectPage"
            @hover-page="hoverProjectPage"
            @cancel-prefetch="cancelPrefetch()"
            @select-docs="emit('select-docs', 'charter')"
          />

          <v-divider class="mx-3 my-1" />

          <div class="t-eyebrow side-subhead side-subhead--row">
            <span>{{ t('work.sidebar.topics') }}</span>
            <BaseButton
              icon="mdi-plus"
              size="sm"
              :title="creatingTopic ? t('work.sidebar.creatingTopic') : t('work.sidebar.newTopic')"
              :aria-label="creatingTopic ? t('work.sidebar.creatingTopic') : t('work.sidebar.newTopic')"
              :loading="creatingTopic"
              :disabled="creatingTopic"
              :class="{ 'tap-target': page }"
              @click="newTopic()"
            />
          </div>

          <LoadingSkeleton v-if="loadingTopics" variant="list" class="rail-skel" />

          <template v-else>
            <!-- 一组都不相关的时候（刚进项目、还没参与任何话题），上组是空的。
                 说清楚「空的是这一组，不是这个项目」，否则下面那个折叠组会像个谜。 -->
            <v-list v-if="mineTree.length === 0 && othersCount > 0" density="compact" nav class="py-0" tabindex="-1">
              <v-list-item class="c-faint t-body">{{ t('work.sidebar.noneMine') }}</v-list-item>
            </v-list>

            <!-- 分组 (C2): 两组走同一段模板。上组直接平铺；下组「其他话题」多一个
                 组头、默认收起。行的形态两组完全一致——见 .group-toggle 的注释。

                 组头长在 <v-list> **外面**，一组一个 <v-list>：`.v-list--nav` 自带
                 8px 的 padding-inline，组头搁在列表里就会比列表外的「已归档」组头
                 右移 8px——两个同款组头一上一下差着一级缩进，「其他话题」读起来像
                 上一条话题的子项。 -->
            <template v-for="section in railSections" :key="section.key">
              <TopicRailGroupToggle
                v-if="section.head"
                :label="section.label"
                :count="section.count"
                :open="section.open"
                :unread="section.unread > 0"
                :unread-title="t('work.sidebar.othersUnread')"
                @toggle="toggleOthers"
              />

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
                    <TopicRailRow
                      :row="item"
                      :selected="item.topic.id === selectedTopicId"
                      :page="page === true"
                      :renaming="renamingTopicId === item.topic.id"
                      :menu-open="actionsMenuFor === item.topic.id"
                      :stalled="stalledOf(item.topic.id)"
                      :muted="mutedOf?.(item.topic.id) ?? false"
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
                  </template>
                </VirtualList>
              </v-list>
            </template>

            <v-list v-if="activeTree.length === 0" density="compact" nav class="py-0" tabindex="-1">
              <v-list-item class="c-faint t-body">{{ t('work.sidebar.empty') }}</v-list-item>
            </v-list>
          </template>

          <!-- 归档去向: collapsed 已归档 group at the bottom of the topic list.
               Archived topics leave the active tree and land here (newest
               first), so done work stops crowding the rail. -->
          <TopicRailArchivedGroup
            :rows="archivedRows"
            :selected-topic-id="selectedTopicId"
            :scroll-parent="railScroll"
            :page="page === true"
            :unread="archivedUnread > 0"
            :unread-of="unreadOf"
            :muted-of="mutedOf"
            @select-topic="emit('select-topic', $event)"
            @hover-topic="emit('hover-topic', $event)"
            @press-topic="emit('press-topic', $event)"
            @leave-topic="emit('leave-topic')"
            @unarchive-topic="emit('unarchive-topic', $event)"
          />
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
  padding: 14px 16px 4px;
}
.side-subhead--row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-block: 6px 4px;
  padding-inline-end: 8px;
}

/* 话题还在路上时，先把行的形状画出来（LoadingSkeleton）。这条 rail 的底是
   --canvas，骨架默认那档 --fill-2 压上去只有 1.083:1，等于什么都没画；--line-2 是
   这条 rail 上「再离底一档」的那个值（选中行用的也是它），在两个主题下都看得见。 */
.rail-skel {
  --skel-bone: var(--line-2);
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
  width: 20px;
  height: 20px;
  font-size: 12px;
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
/* 首字母头像：人的（.dm-avatar，圆）和项目的（.project-avatar，方）同一套底子，
   18px，图标列和文字列才对得齐。 */
.dm-avatar,
.project-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  /* The --fill here is only the pre-paint placeholder: the real ground is
     avatarColor() bound inline in the template, a fixed hsl that is the same in
     both themes — so the initial on it stays a literal #fff. */
  background: var(--fill);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
}
/* 项目头像：和人的头像同一个底子（.dm-avatar），只换形状——方头像，和桌面那条
   竖 rail 上一个项目一格的画法是同一种语言。人是靠方/圆区分「这是个项目」还是
   「这是个人」的，都画成圆的就混了。 */
.project-avatar {
  border-radius: var(--radius-sm);
}
/* 整页形态：手指点的地方至少 44px 高。行和组头那两个组件自己也各写了一条（它们
   要能单独渲染），这里这条管的是这一层画不出来的部分。 */
.topic-rail--page .group-toggle {
  min-height: 44px;
}
</style>
