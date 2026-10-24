<template>
  <my-app>
    <!-- Skip-to-content: the shell's first focusable element, so one Tab lands
         on it. Kept out of view until focused (see .skip-link). Target is the
         <main> below (id="main-content" + tabindex="-1"). -->
    <a class="skip-link" href="#main-content" @click.prevent="skipToContent">{{
      t('navigation.shell.skipToContent')
    }}</a>
    <!-- 桌面端：使用 StatusBar -->
    <template v-if="$vuetify.display.mdAndUp">
      <keep-alive>
        <app-bar v-if="!hideAppBar" :links="[]" :logged-in="loggedIn" />
      </keep-alive>
      <keep-alive>
        <LeftAppRail v-if="!hideAppBar" :items="rail" @reorder="reorderRail" />
      </keep-alive>

      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />
    </template>
    <template v-else>
      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />

      <!-- 移动端：唯一的一条顶栏，从不卸载。中间画的是当前这一屏声明的头部
           （composables/useScreenHeader.ts）——挂上/卸下这条横条会让 v-main 的
           padding 滑一下，页面跟着抖。 -->
      <mobile-app-bar v-if="!hideAppBar" />

      <!-- 一级导航：桌面端左侧 Rail，移动端底部。页面栈里的那几层（hideTabs）没有它；
           它进出时 v-main 的下内边距跟着变，手机上这一下不做过渡（见 .app-main--phone）。 -->
      <keep-alive>
        <BottomAppBar v-if="!hideAppBar && !hideTabs" :items="tabs" />
      </keep-alive>
    </template>

    <v-main
      id="main-content"
      ref="mainRef"
      class="bg-background h-100"
      :class="{
        'app-main--pending': firstRoutePending,
        'app-main--phone': !$vuetify.display.mdAndUp,
        'app-main--tabs': !hideAppBar && !hideTabs,
      }"
      tabindex="-1"
    >
      <!-- 内容区是一整块 surface，外框（一级导航、侧栏、顶栏）是 canvas：设计规范 §1.4。
           左边那条线就是侧栏和内容的分界；没有侧栏的页面，这块面挨着一级导航，左上角
           拐成和侧栏一样的圆角。 -->
      <div class="app-pane h-100 overflow-hidden" :class="{ 'app-pane--alone': !hasSidebar }">
        <div id="app-scrollable" ref="contentRef" class="app-content h-100" @animationend="endPageMotion">
          <!-- 保活是白名单，不是黑名单。缓存一个页面组件等于把它的表单、它的
               「上一个人是谁」一起留在内存里 —— 登录/注册/OAuth 回调/验证码那
               几页要是被留下来，退出后再登录会看到上一个账号的填写状态。所以
               这里只点名那些「进过一次就该立刻回来」的项目内页面，其余一律照
               旧挂载/卸载。:max 是内存上限，别去掉。

               v-memo="[]" 是这里的必需品，不是优化。带 v-slot 的 router-view 就
               有了 slot，而 Vue 对「有 slot 的子组件」在父组件重渲染时一律强制更
               新；RouterView 每次重渲染都给页面组件换一个新的 onVnodeUnmounted，
               于是页面组件也跟着在**父组件的 patch 中途**重渲染，排在外框自己
               那一轮更新的半路上（比如断点切换时顶栏、底栏正在换）。空的依赖数组
               让这棵子树不再被父组件的重渲染碰到（路由自己的重渲染照常），也就
               是加 v-slot 之前 router-view 本来的样子。想挪动它、或者改白名单
               之前，先看 App.keepAlive.spec.ts：那三条用例分别钉住「白名单里的
               页面被保活」「登录页不被保活」「切路由确实换页」，把 v-memo 挪进
               下面这个 <component> 三条会一起变红。 -->
          <router-view v-slot="{ Component }" v-memo="[]">
            <keep-alive :include="keptAlivePages" :max="5">
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </div>
      </div>
    </v-main>

    <!-- 协议实质变更后的重新同意（#1486）；只在应用外壳里，协议页不在外壳里。
         取数和两个去处见 usePendingConsent，这里只接线。 -->
    <ConsentGate
      :pending="pendingConsents"
      :accepting="acceptingConsents"
      :error="consentError"
      @accept="acceptConsents"
      @decline="declineConsents"
    />

    <!-- 敏感操作前确认身份；withSudo 打开它 -->
    <SudoDialog v-if="sudoWanted" />

    <!-- The main app's keyboard shortcut sheet (? or the keyboard button by the message box). -->
    <AppShortcutSheet />

    <NewProjectDialog />

    <!-- 内测: running-build badge, self-hides unless the box opted in. -->
    <VersionBadge :version="appVersion" />

    <!-- 离线指示: shows only while offline, auto-hides when the network returns. -->
    <OfflineBanner />

    <!-- The desktop app's 关于 and 在手机上使用 dialogs, opened from menus that close as they do. -->
    <template v-if="inApp">
      <DesktopAboutDialog />
      <DesktopPhoneDialog />
    </template>
    <CommandPalette />
    <!-- The person's own assistant: one panel for the whole app (stores/agent.ts). -->
    <AgentHost />
    <DeviceConnect />
    <!-- 新手引导的后半程：一个浮窗一步步带着把第一件事做完（composables/useProjectTour.ts）。
         挂在最外层：任务页、支线里也要接着带。队友写项目给它起的名字：「界面导览」在改过名的项目里也能重走。 -->
    <GuideBubble
      v-if="guideStep"
      :target="guideStep.target"
      :ring="!guideStep.area"
      :title="t(`welcome.guide.${guideStep.key}.title`, { agent: workspace.agentName })"
      :text="
        t(`welcome.guide.${guideStep.key}.${guideStep.text}`, {
          agent: workspace.agentName,
        })
      "
      :step="guide.stop.value + 1"
      :total="guide.total"
      :can-next="!guideStep.action"
      @next="guide.next()"
      @back="guide.back()"
      @skip="guide.skip()"
    />
    <!-- 「知是正在内测」：每个账号说一次有哪些额度可拿（composables/useBetaNotice.ts）。 -->
    <BetaNoticeDialog
      v-if="betaNotice.offer.value"
      :open="betaNotice.open.value"
      :offer="betaNotice.offer.value"
      :claiming="betaNotice.claiming.value"
      :claim-failed="betaNotice.claimFailed.value"
      @claim="betaNotice.claim()"
      @dismiss="betaNotice.dismiss()"
    />
    <!-- 第一次采纳之后提醒一次：可以邀请好友了（composables/useInvitePrompt.ts）。 -->
    <InvitePromptCard
      v-if="invitePrompt.open.value"
      open
      :invites-to="{ name: 'UserSettingsInvites' }"
      @copy-link="invitePrompt.copySignupLink()"
      @dismiss="invitePrompt.dismiss()"
    />
    <!-- 右键 rail 上一个项目「退出项目」：和成员页、项目菜单是同一个确认框。 -->
    <LeaveProjectDialog v-if="leavingProjectId" v-model="leaveOpen" :project-id="leavingProjectId" />
  </my-app>
</template>

<script setup lang="ts">
// 应用的外框：桌面上的顶栏、左边那一列和侧栏，手机上的顶栏和底栏，中间装页面的内容区，
// 以及挂在最外层、哪一页都可能叫出来的那些弹层（命令面板、新建项目、引导气泡……）。
// 门口页面（官网、方案）不在框里，App.vue 在两者之间选。
//
// 这个框会随着在门口页面和应用之间来回而卸下、重挂，所以它装上的路由钩子和命令都在
// 卸下时摘掉，免得来回一趟多一份。
import type { NavSources } from '@/components/common/Navigation/destinations'
import type { Project } from '@/cx_types'

import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'
import { useQuery } from '@tanstack/vue-query'

import { scrollBehavior } from '@/utils/motion'
import { pendingSudo } from '@/utils/sudo'

import { awaitingCountByProject, useAwaitingCount } from '@/composables/useAwaitingCount'
import { useBetaNotice } from '@/composables/useBetaNotice'
import { guideAnchor, useGuideAnchorRevision } from '@/composables/useGuideAnchors'
import { useInvitePrompt } from '@/composables/useInvitePrompt'
import { teamHandleInPath, useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { usePendingConsent } from '@/composables/usePendingConsent'
import { useProjectMenu } from '@/composables/useProjectMenu'
import { resolveGuideStep, useProjectTour } from '@/composables/useProjectTour'
import { useUnreadNotifications } from '@/composables/useUnreadNotifications'
import { useWorkspaceLayout } from '@/composables/useWorkspaceLayout'

import { type AppVersion, getAppVersion } from '@/api'
import { defineCommands } from '@/commands'
import { copyLink } from '@/commands/copy'
import ConsentGate from '@/components/account/ConsentGate.vue'
import AppShortcutSheet from '@/components/common/AppShortcutSheet.vue'
import MyApp from '@/components/common/MyApp.vue'
import AppBar from '@/components/common/Navigation/AppBar.vue'
import BottomAppBar from '@/components/common/Navigation/BottomAppBar.vue'
import {
  railItems,
  railShortcut,
  shortcutTarget,
  tabItems,
  workspaceProject,
} from '@/components/common/Navigation/destinations'
import LeftAppRail from '@/components/common/Navigation/LeftAppRail.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'
import OfflineBanner from '@/components/common/OfflineBanner.vue'
import VersionBadge from '@/components/common/VersionBadge.vue'
import { t } from '@/i18n'
import AgentHost from '@/layouts/AgentHost.vue'
import { routeIds } from '@/lib/addresses'
import { firstPaintDone } from '@/lib/afterFirstPaint'
import { rememberRailCount } from '@/lib/bootShape'
import { desktopBadge, inDesktopApp } from '@/lib/desktopApp'
import { landBootSplash } from '@/lib/desktopSplash'
import { pageMotion } from '@/lib/pageMotion'
import {
  applyProjectOrder,
  type DropEdge,
  loadProjectOrder,
  reorderProjects,
  saveProjectOrder,
} from '@/lib/projectOrder'
import { DEFAULT_SHELL, shellFor, termParams } from '@/lib/shell'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { projectsQuery, refreshProjects } from '@/query/projects'
import AccountService from '@/services/account'
import { lastOpenedProjectId, useWorkspaceStore } from '@/stores/workspace'
import { useAppTheme } from '@/theme'
import NewProjectDialog from '@/views/workspace/NewProjectDialog.vue'

const currentRoute = useRoute()
const router = useRouter()
// 命令面板里那几条外观选项。
const appTheme = useAppTheme()
const workspace = useWorkspaceStore()

// 手机上换页的那一下（桌面上没有）：新页从右边（往里走一层）或左边（退回一层）
// 挪进来 24px 并淡入，平级切换（底栏换格）只淡入。方向由 lib/pageMotion 按路由声明的
// 上一层（backTo）算，不按浏览器历史。
//
// 只演「进来」，旧页随这次渲染直接换掉：两页同时在场的整屏滑动要让离开的那页多活
// 一段，它往顶栏里传送的标题会和新页的叠成两份，保活和滚动位置也要跟着绕。24px 的
// 位移加淡入已经说清了方向。动的是装页面的那一层（#app-scrollable），顶栏和底栏不
// 动：它们是框，不是页。减弱动效时不演（见样式）。
const display = useDisplay()
const workspaceLayout = useWorkspaceLayout()
const contentRef = ref<HTMLElement | null>(null)
const MOTION_CLASSES = ['page-enter--forward', 'page-enter--back', 'page-enter--fade']
const stopPageMotion = router.afterEach((to, from, failure) => {
  if (failure || display.mdAndUp.value) return
  const motion = pageMotion(to, from, router, workspaceLayout.value === 'split')
  if (!motion) return
  // 等新页画进 DOM（同一个微任务里、浏览器上屏之前）再起步，第一帧就是它的起点。
  void nextTick(() => {
    const el = contentRef.value
    if (!el) return
    el.classList.remove(...MOTION_CLASSES)
    // 连着换两页时从头再演一次：先让浏览器认下「没有动画」，再加回去。
    void el.offsetWidth
    el.classList.add(`page-enter--${motion}`)
  })
})
onBeforeUnmount(stopPageMotion)

// 外框挂着的时候整页不滚，滚的是内容区（#app-scrollable）；规则在 styles/common.scss。
// 由外框自己加、自己摘，所以不进外框的页面（门口页、法律文本、分享页）照常整页滚动，
// 不用各自去解锁。
onMounted(() => document.documentElement.classList.add('app-shell-open'))
onBeforeUnmount(() => document.documentElement.classList.remove('app-shell-open'))

function endPageMotion(event: AnimationEvent) {
  if (event.target === contentRef.value) contentRef.value?.classList.remove(...MOTION_CLASSES)
}

// 名字来自各自组件里的 defineOptions({ name })。保活的页面离开时组件还在，它的查询
// 把 `enabled` 绑到页面在不在屏幕上（composables/usePageActive）：离开时不跟着别处的
// 失效重读，回来时过期了的再读一次。
const keptAlivePages = ['ProjectDocsView', 'ProfileView']

// 确认身份的弹窗第一次被要用时才加载：大多数会话从不需要它
const SudoDialog = defineAsyncComponent(() => import('@/components/account/SudoDialog.vue'))
const inApp = inDesktopApp()
const DesktopAboutDialog = defineAsyncComponent(() => import('@/components/common/DesktopAboutDialog.vue'))
const DesktopPhoneDialog = defineAsyncComponent(() => import('@/components/common/DesktopPhoneDialog.vue'))
// 只在有人打开、或某件事发生时才出现的几样，不跟外壳一起下：冷打开时都用不上。
const CommandPalette = defineAsyncComponent(() => import('@/commands/palette/CommandPalette.vue'))
const DeviceConnect = defineAsyncComponent(() => import('@/views/desktop/DeviceConnect.vue'))
const GuideBubble = defineAsyncComponent(() => import('@/components/common/GuideBubble.vue'))
const LeaveProjectDialog = defineAsyncComponent(() => import('@/components/LeaveProjectDialog.vue'))
const InvitePromptCard = defineAsyncComponent(() => import('@/components/invites/InvitePromptCard.vue'))
const BetaNoticeDialog = defineAsyncComponent(() => import('@/components/usage/BetaNoticeDialog.vue'))
const sudoWanted = ref(false)
watch(pendingSudo, (request) => {
  if (request) sudoWanted.value = true
})

const hideAppBar = computed(() => {
  return currentRoute.meta.hideAppBar
})

/** 顶栏要的登录态。外壳是布局，可以直接读 `services/account`；`AppBar` 在
 *  `src/components/**` 下，不行，所以从这里传下去（`.claude/rules/architecture.md`）。 */
const loggedIn = computed(() => AccountService.loggedIn)

// 页面栈的末端（话题页、私聊页）收起底栏：它们是栈里的一层，不是一级目的地。
const hideTabs = computed(() => currentRoute.meta.hideTabs === true)
/** 这一页有没有侧栏：侧栏是路由上的 `sidebar` 命名视图，渲染它的是上面那个 router-view。 */
const hasSidebar = computed(() => currentRoute.matched.some((record) => record.components?.sidebar))

// Fusion merge (C): 项目来自我们的后端 (/api/projects)，在桌面 rail 上一个项目
// 一格方头像（Discord 式，取代了原来的元思助手），点开的是我们的完整工作区
// (话题/群聊/doc/agent)。两端各拿到哪些格子由 Navigation/destinations.ts 说了算。
// 冷打开时上一次的清单从本标签页的存储里先拿出来（query/persist），rail 当场就有。
const projectsRead = useQuery(
  computed(() => ({ ...projectsQuery(), enabled: AccountService.loggedIn })),
  queryClient
)
const cxProjects = computed<Project[]>(() => (AccountService.loggedIn ? projectsRead.data.value ?? [] : []))

// 服务端那份清单是 created_at desc，rail 画的是这个人自己拖出来的顺序。两者分开
// 存：拖过之后再刷新项目列表，排法不会被服务端的顺序盖掉。
const projectOrder = ref<string[]>(loadProjectOrder(myHandle()))
const railProjects = computed(() => applyProjectOrder(cxProjects.value, projectOrder.value))
// 下次冷打开时启动骨架照这个数画 rail 上的项目格。
rememberRailCount(() => (AccountService.loggedIn && projectsRead.data.value ? cxProjects.value.length : null))

function reorderRail(movedId: string, targetId: string, edge: DropEdge) {
  const next = reorderProjects(railProjects.value, movedId, targetId, edge)
  projectOrder.value = next
  saveProjectOrder(myHandle(), next)
}

// 一次性提示：之前是一条常驻的 snackbar，现在并进全局 toast（§3.11）。
watch(
  () => projectsRead.error.value,
  (error) => {
    if (!error) return
    const message = cxProjects.value.length ? t('work.projectList.stale') : t('work.projectList.unavailable')
    toast.warning(message, {
      duration: 8000,
      action: { label: t('work.newProject.retry'), onClick: () => void projectsRead.refetch() },
    })
  }
)

// 首屏先画外壳（左栏），内容区等第一个路由画好再露出来。路由的懒加载 chunk 还没
// 到的时候，项目侧栏也还没注册，内容区先按没有侧栏的宽度画出来，侧栏一到整块内容
// 往右跳一个侧栏宽——每个项目页冷打开时最大的一次布局偏移。在那之前内容区不可见、
// 也不做内边距过渡，露出来的时候已经在最终位置上。
const firstRoutePending = ref(true)
const mainRef = ref<{ $el: Element } | null>(null)

/** 跳到正文：把焦点落到 <main>（要 tabindex="-1" 才接得住），并把它滚进视野。
 *  滚动的快慢跟着「减弱动效」走（utils/motion）。 */
function skipToContent(): void {
  const el = mainRef.value?.$el
  if (!(el instanceof HTMLElement)) return
  el.focus()
  el.scrollIntoView({ block: 'start', behavior: scrollBehavior() })
}

// 内测版本徽标要画的那个构建。壳来问，徽标只画：取不到（接口不通、后端太旧）就不画。
// 徽标是装饰，所以排到首帧之后（`lib/afterFirstPaint.ts`），别在开页那一瞬间跟页面
// 自己要的数据抢。
const appVersion = ref<AppVersion | null>(null)
watch(
  firstPaintDone,
  async (painted) => {
    if (!painted || appVersion.value) return
    try {
      appVersion.value = await getAppVersion()
    } catch {
      // Best-effort: no badge if the endpoint is unreachable or old.
    }
  },
  { immediate: true }
)

onMounted(async () => {
  await router.isReady().catch(() => {})
  await nextTick()
  // 读一次内边距，让侧栏撑出来的那个值在「不做过渡」时先落定；否则撤掉
  // pending 和内边距变化挤在同一次样式计算里，过渡照样会演。不等帧：挂载时正
  // 有重活（登录页的 WebGL 场景）的话，等下一帧会把整个内容区拖后几百毫秒。
  if (mainRef.value) void getComputedStyle(mainRef.value.$el).paddingLeft
  firstRoutePending.value = false
  // The rail is laid out by the next frame; the desktop app's boot splash lands in it.
  requestAnimationFrame(() => void landBootSplash())
})

// A project can appear from outside this dialog — made on a team page, or by
// a teammate — and the rail would keep showing the cached list until a reload.
// Opening a project the rail does not know is the cheapest signal that the
// list is stale, so reload it then.
watch(
  () => workspace.projectId,
  (id) => {
    if (id && AccountService.loggedIn && !cxProjects.value.some((p) => p.id === id)) void refreshProjects()
  }
)

// …and again whenever the identity changes. The rail used to load exactly once,
// on mount — and the app normally mounts on the sign-in page, i.e. with no
// credential yet. Signing in is an SPA navigation, not a reload, so nothing
// ever fetched the list again: the rail a user looked at all session was the
// one fetched as nobody.
//
// While the backend answered an unidentifiable caller with EVERY project, that
// was invisible-but-wrong — a full rail of other people's work. Now that it
// answers with none, the same gap would leave a signed-in user staring at an
// empty rail forever, which is how the e2e suite caught this.
//
// `loggedIn` flips before `accessToken` is written inside AccountService.login,
// but this watcher is not `flush: 'sync'`, so the callback runs after that
// synchronous call has finished and the token is in storage.
watch(
  () => AccountService.loggedIn,
  () => {
    // 排法是按 handle 存的，所以换了人就得换一份读进来——否则新登录的人看到的是
    // 上一个人的排法，直到下一次整页刷新。
    projectOrder.value = loadProjectOrder(myHandle())
    if (AccountService.loggedIn) void projectsRead.refetch()
  }
)

// 上次开过的那个项目存在 workspace store 的布局里，所以冷启动也落得回去。
const workspaceProjectId = computed<string | null>(() =>
  workspaceProject(railProjects.value, workspace.projectId, lastOpenedProjectId())
)

// 待我处理的件数：桌面画在首页那一格上，手机画在底栏「待办」上。
const awaitingCount = useAwaitingCount(computed(() => AccountService._loggedIn.value))
// 同一个数按项目拆开：桌面 rail 的每个项目格子画自己那几件。
const awaitingByProject = awaitingCountByProject()
// 没读的动态（提到你、回复你……）：没有待处理的事时，同一格上画一颗小点。
const { count: unreadActivity } = useUnreadNotifications()
// The same number on the desktop app's icon, whenever this page has read it.
watch(awaitingCount, desktopBadge)

// 协议实质变更后的重新同意（#1486）：取数在 composable 里，这里只把它接到弹窗上。
const {
  pending: pendingConsents,
  checked: consentsChecked,
  accepting: acceptingConsents,
  error: consentError,
  accept: acceptConsents,
  decline: declineConsents,
} = usePendingConsent()

// 右键 rail 上一个项目：那一份菜单和退出确认框的状态见 useProjectMenu。
const { projectMenu, leaveOpen, leavingProjectId } = useProjectMenu(router)

const navSources = computed<NavSources>(() => ({
  projects: railProjects.value,
  workspaceProjectId: workspaceProjectId.value,
  createProject: createNewProject,
  projectMenu,
  awaitingCount: awaitingCount.value,
  projectAwaitingCount: (id) => awaitingByProject.value[id] ?? 0,
  projectLastPlace: (id) => workspace.placeFor(id),
  unreadActivity: unreadActivity.value > 0,
}))

// 壳 (shell)：**地址里那个项目**的壳决定这份导航怎么画。不在项目里（首页、空间、
// 设置、某个 赛题 页）时是 default——那里没有项目行可读，而 default 就是今天的
// 样子，所以项目外的一点都没变。按地址取而不是按「上次开过的项目」取：壳是**你
// 现在待的地方**的长相，走出项目还挂着上一个项目的样子会让人以为走岔了。
const openProjectId = computed<string | null>(() => routeIds(currentRoute.params).projectId ?? null)
const navShell = computed(() => shellFor(railProjects.value, openProjectId.value) ?? DEFAULT_SHELL)

const rail = computed(() => railItems(navSources.value, navShell.value))

// rail 的悬停浮层写着第 N 格的快捷键；这里是它真正被绑上的地方。浏览器里是 G 然后 N，
// 桌面 app 里是 ⌘N（railShortcut 说了为什么）。只有真的对上某一格的数字才登记。
onBeforeUnmount(
  defineCommands(() =>
    [1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((digit) => {
      const to = shortcutTarget(rail.value, digit)
      const item = rail.value.find((it) => it.type === 'item' && it.to === to)
      const title = item?.type === 'item' ? item.title : to
      return to
        ? [
            {
              id: `rail.${digit}`,
              title: title ?? to,
              shortcut: railShortcut(digit, inApp).shortcut,
              to,
              palette: false as const,
            },
          ]
        : []
    })
  )
)
// 新手引导此刻在哪一步、指着什么。动作步做到了自己往下走（`settle`），所以页面上一
// 出现新东西（登记表一变）、服务端的事实一变，就重新判一次。
const guide = useProjectTour()
// 新手引导还在带、或者协议弹窗还开着，邀请提醒就等一等，不和它们抢。
const invitePrompt = useInvitePrompt(() => guide.active.value || pendingConsents.value.length > 0)
// 内测说明等协议问完、新手引导确定不带（走完了，或者问回来这个人已经会了）、邀请小卡
// 不在了才出来，免得刚出来又被别的盖住。反过来不用等：说明是个模态框，开着的时候采纳
// 不了，邀请小卡也就到不了期。
const betaNotice = useBetaNotice(
  () =>
    !consentsChecked.value ||
    pendingConsents.value.length > 0 ||
    guide.active.value ||
    (!guide.done.value && guide.facts.value === null) ||
    invitePrompt.open.value
)
const guideRevision = useGuideAnchorRevision()
const guideHas = (name: string) => !!guideAnchor(name)
watch(
  [guideRevision, guide.facts, guide.active],
  () => {
    if (guide.active.value && guide.facts.value) guide.settle({ facts: guide.facts.value, has: guideHas })
  },
  { immediate: true }
)
const guideStep = computed(() => {
  void guideRevision.value
  const step = guide.current.value
  if (!guide.active.value || !step) return null
  return resolveGuideStep(step, guideAnchor, { replaying: guide.replaying.value })
})
const tabs = computed(() => tabItems(navSources.value, navShell.value))
// The "+" rail affordance opens the new-project dialog (views/workspace/NewProjectDialog.vue).
const { show: showNewProjectDialog } = useNewProjectDialog()

function createNewProject() {
  // From a team page, that team; elsewhere the dialog falls back to the caller's own name.
  showNewProjectDialog(teamHandleInPath(currentRoute.path))
}

// 不属于哪一页、在哪都能做的事：命令面板的「操作」里有它们。
onBeforeUnmount(
  defineCommands(() => [
    {
      id: 'project.new',
      title: t('navigation.newProject', termParams(navShell.value)),
      icon: 'mdi-plus',
      run: createNewProject,
    },
    {
      id: 'page.copyLink',
      title: t('navigation.palette.copyLink'),
      icon: 'mdi-link-variant',
      run: () => void copyLink(window.location.href),
    },
    // 外观只列另外两种：当前这种不用选。
    ...appTheme.options
      .filter((mode) => mode !== appTheme.preference.value)
      .map((mode) => ({
        id: `theme.${mode}`,
        title: t('navigation.palette.theme', { mode: t(`navigation.userMenu.theme.${mode}`) }),
        icon:
          mode === 'dark' ? 'mdi-weather-night' : mode === 'light' ? 'mdi-white-balance-sunny' : 'mdi-theme-light-dark',
        run: () => appTheme.setPreference(mode),
      })),
  ])
)
</script>

<style lang="scss" scoped>
.app-main--pending {
  visibility: hidden;
  transition: none;
}
/* 手机上 v-main 的内边距只随底栏的有无变（顶栏从不卸载）。Vuetify 给 .v-main 定了
   .2s 的内边距过渡，于是每走进、退出一层页面栈，整页内容都要滑 56px——底栏已经
   不在了，内容还在往下挪。这一下跟着换页一起完成，不单独演。 */
.app-main--phone {
  transition: none;
  /* 刘海 / 状态栏：顶栏自己让了 `safe-area-inset-top`（style.css），但 Vuetify 量
     `--v-layout-top` 用的是它标称的 56px，内容区因此照旧停在 56——多出来的那一截
     被顶栏压住。补上同一截就对上了；没有顶栏的页面（账号那几页 hideAppBar）这边
     `--v-layout-top` 是 0，补的就是裸的安全区，内容也不会钻进刘海底下。

     底边不在这里无条件补：顶栏是每页都在的，底栏不是——没有底栏的页面（话题页）
     最底下那件东西是输入区，安全区由它自己出（见 ChatPanel.vue 那段注释），这里
     再补一次会叠出两倍的空。所以底边只在底栏真挂着的页面上补（`--tabs`）。

     `env()` 在桌面和无安全区的设备上是 0，整条在那里是空操作。 */
  padding-top: calc(var(--v-layout-top, 0px) + env(safe-area-inset-top, 0px));
}
.app-main--phone.app-main--tabs {
  padding-bottom: calc(var(--v-layout-bottom, 0px) + env(safe-area-inset-bottom, 0px));
}
.page-enter--forward {
  animation: page-enter-forward var(--dur-base) var(--ease-standard) backwards;
}
.page-enter--back {
  animation: page-enter-back var(--dur-base) var(--ease-standard) backwards;
}
.page-enter--fade {
  animation: page-enter-fade var(--dur-base) var(--ease-standard) backwards;
}
@keyframes page-enter-forward {
  from {
    opacity: 0;
    transform: translateX(24px);
  }
}
@keyframes page-enter-back {
  from {
    opacity: 0;
    transform: translateX(-24px);
  }
}
@keyframes page-enter-fade {
  from {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .page-enter--forward,
  .page-enter--back,
  .page-enter--fade {
    animation: none;
  }
}

/* 跳到正文：外壳的第一个可聚焦元素。平时藏在屏幕上沿之外，键盘聚焦时才滑下来，
   露出琥珀色焦点环（全局 :focus-visible 那条）。只动 transform，不碰其他属性 ——
   设计规范 §9；减弱动效由 style.css 末尾的全局规则统一压掉。 */
/* 跳转目标只是落点，不是控件：程序化 focus 之后不给整个内容区画一圈焦点环。 */
#main-content:focus {
  outline: none;
}

.skip-link {
  position: fixed;
  /* 出现在左上角：让出顶部与左侧安全区，刘海机上聚焦时不压进状态栏 / 圆角。桌面上
     `env()` 是 0，位置不变。桌面版 macOS 里还要让开三颗窗口按钮（style.css 的
     --window-buttons-inset，别处恒为 0）——它们画在网页上层，会让这一颗被盖住。 */
  top: calc(10px + env(safe-area-inset-top, 0px));
  left: max(calc(16px + env(safe-area-inset-left, 0px)), var(--window-buttons-inset));
  z-index: var(--z-banner); /* 压在顶栏和抽屉之上，和 OfflineBanner 同一档 */
  padding: 8px 14px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
  text-decoration: none;
  background: var(--raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-2);
  transform: translateY(-160%);
  transition: transform var(--dur-quick) var(--ease-out);
}
.skip-link:focus,
.skip-link:focus-visible {
  transform: none;
}

.app-content {
  min-height: 0;
  overflow: auto;
  /* contain only the vertical axis: `contain` on both axes also swallows the
     browser's horizontal swipe-to-go-back gesture (fusion: 左划返回失效). */
  overscroll-behavior-y: contain;
}
</style>
