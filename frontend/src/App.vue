<template>
  <router-view v-if="currentRoute.meta.publicLanding" />
  <my-app v-else>
    <!-- Skip-to-content: the shell's first focusable element, so one Tab lands
         on it. Kept out of view until focused (see .skip-link). Target is the
         <main> below (id="main-content" + tabindex="-1"). -->
    <a class="skip-link" href="#main-content" @click.prevent="skipToContent">{{
      t('navigation.shell.skipToContent')
    }}</a>
    <!-- 桌面端：使用 StatusBar -->
    <template v-if="$vuetify.display.mdAndUp">
      <keep-alive>
        <app-bar v-if="!hideAppBar" :links="[]" />
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

      <!-- 移动端：唯一的一条顶栏，从不卸载。内容由当前页填（MobileAppBar 里的
           #app-bar-slot）——挂上/卸下这条横条会让 v-main 的 padding 滑一下，
           页面跟着抖。 -->
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
      :class="{ 'app-main--pending': firstRoutePending, 'app-main--phone': !$vuetify.display.mdAndUp }"
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
               于是页面组件也跟着在**父组件的 patch 中途**重渲染。断点从桌面切到
               手机时这个中途正好排在移动顶栏挂上之前，Home 的 Teleport 因此找不
               到 #app-bar-slot：首页的分段 tab 消失，卸载时还会崩。空的依赖数组
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

    <!-- 协议实质变更后的重新同意（#1486）；只在应用外壳里，协议页不在外壳里 -->
    <ConsentGate />

    <!-- 敏感操作前确认身份；withSudo 打开它 -->
    <SudoDialog v-if="sudoWanted" />

    <!-- 新建项目 (opened by the rail's "+" affordance)。要填好几项，手机上是整页：
         下一步 / 创建在页头右边，键盘弹起来也够得着。 -->
    <AdaptiveDialog
      v-model="newProjectDialog"
      :title="newProjectStep === 1 ? t('work.newProject.title') : t('work.teammate.title')"
      :primary-label="newProjectStep === 1 ? t('work.teammate.next') : t('work.teammate.create')"
      :primary-loading="creatingProject"
      :primary-disabled="
        !newProjectName.trim() ||
        loadingTeams ||
        newProjectTeamId === null ||
        !!teamLoadError ||
        (newProjectStep === 2 && !newProjectAgentName.trim())
      "
      :cancel-label="t('work.newProject.cancel')"
      :close-disabled="creatingProject"
      :max-width="420"
      persistent
      @primary="newProjectStep === 1 ? advanceNewProject() : confirmNewProject()"
    >
      <div v-show="newProjectStep === 1">
        <p v-if="sourceTask" class="t-body c-muted mb-3">
          {{ t('work.newProject.fromTask', { task: sourceTask.name }) }}
        </p>
        <!-- 从一道题建项目时，先把「会继承什么」摆出来 (#944)：资源包、合成后的
             指导（连来自哪一层）、以及会被带上的资料。建之前看得见，才有得选。 -->
        <TaskInheritance
          v-if="sourceTask"
          class="mb-3"
          :inheritance="sourceInheritance"
          :loading="sourceInheritanceLoading"
        />
        <ResourceLimitsNotice
          v-if="newProjectDialog"
          :own="newProjectTeams.find((team) => team.id === newProjectTeamId)?.personal"
        />
        <v-text-field
          v-model="newProjectName"
          autocomplete="off"
          :label="t('work.newProject.name')"
          variant="outlined"
          color="primary"
          autofocus
          hide-details
          :disabled="creatingProject"
          @keyup.enter="advanceNewProject"
        />
        <!-- 可选：答案会跟着项目进房间（见 ProjectService.create）。不填也能建，
             所以这不是必填项，标签里就写着「可选」。 -->
        <v-textarea
          v-model="newProjectIntent"
          autocomplete="off"
          :label="t('work.newProject.intent')"
          :placeholder="t('work.newProject.intentExample')"
          variant="outlined"
          color="primary"
          rows="2"
          auto-grow
          hide-details
          class="mt-3"
          :disabled="creatingProject"
        />
        <v-select
          v-model="newProjectTeamId"
          autocomplete="off"
          :items="newProjectTeams"
          item-title="name"
          item-value="id"
          :item-props="teamItemProps"
          :label="t('work.newProject.team')"
          variant="outlined"
          color="primary"
          class="mt-3"
          hide-details
          :loading="loadingTeams"
          :disabled="creatingProject || loadingTeams"
        />
        <v-select
          v-model="newProjectForgeKind"
          autocomplete="off"
          :items="[
            { title: t('work.newProject.forgeHosted'), value: 'forgejo' },
            { title: t('work.newProject.forgeGithub'), value: 'github_app' },
          ]"
          :label="t('work.newProject.forge')"
          variant="outlined"
          color="primary"
          class="mt-3"
          hide-details
          :disabled="creatingProject"
        />
        <div class="t-meta-read mt-2">
          {{
            newProjectForgeKind === 'forgejo' ? t('work.newProject.forgeHint') : t('work.newProject.forgeGithubHint')
          }}
        </div>
        <v-alert v-if="teamLoadError" type="error" density="compact" variant="tonal" class="mt-3">
          {{ teamLoadError }}
          <BaseButton kind="secondary" size="sm" :loading="loadingTeams" @click="loadProjectTeams">{{
            t('work.newProject.retry')
          }}</BaseButton>
        </v-alert>
      </div>
      <div v-if="newProjectStep === 2">
        <p class="t-body mb-4">{{ t('work.teammate.intro', { project: newProjectName.trim() }) }}</p>
        <v-text-field
          v-model="newProjectAgentName"
          :label="t('work.teammate.name')"
          variant="outlined"
          autocomplete="off"
          maxlength="64"
          :disabled="creatingProject"
          @keyup.enter="confirmNewProject"
        >
          <template #append-inner>
            <BaseButton
              icon="mdi-dice-multiple-outline"
              size="sm"
              :aria-label="t('work.teammate.random')"
              :title="t('work.teammate.random')"
              :disabled="creatingProject"
              @click="newProjectAgentName = randomTeammateName(newProjectAgentName)"
            />
          </template>
        </v-text-field>
        <p class="t-meta-read">{{ t('work.teammate.more') }}</p>
      </div>
      <v-alert v-if="newProjectError" type="error" density="compact" variant="tonal" class="mt-3">
        {{ newProjectError }}
      </v-alert>
      <template v-if="newProjectStep === 2" #actions>
        <BaseButton kind="ghost" :disabled="creatingProject" @click="newProjectStep = 1">{{
          t('work.teammate.back')
        }}</BaseButton>
      </template>
    </AdaptiveDialog>

    <v-snackbar v-model="showProjectListWarning" :timeout="8000">
      {{ projectListWarning }}
      <template #actions>
        <BaseButton kind="secondary" @click="loadCxProjects">{{ t('work.newProject.retry') }}</BaseButton>
      </template>
    </v-snackbar>

    <!-- 内测: running-build badge, self-hides unless the box opted in. -->
    <VersionBadge />

    <!-- 离线指示: shows only while offline, auto-hides when the network returns. -->
    <OfflineBanner />

    <!-- The desktop app's 关于 and 在手机上使用 dialogs, opened from menus that close as they do. -->
    <template v-if="inApp">
      <DesktopAboutDialog />
      <DesktopPhoneDialog />
    </template>
    <CommandPalette />
    <!-- 右键 rail 上一个项目「退出项目」：和成员页、项目菜单是同一个确认框。 -->
    <LeaveProjectDialog v-if="leavingProjectId" v-model="leaveOpen" :project-id="leavingProjectId" />
  </my-app>
</template>

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'
import type { Project } from '@/cx_types'
import type { Team } from '@/types/teams'
import type { NavSources } from './components/common/Navigation/destinations'

import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { avatarColor } from '@/utils/avatar'
import { scrollBehavior } from '@/utils/motion'
import { pendingSudo } from '@/utils/sudo'

import { useAwaitingCount } from '@/composables/useAwaitingCount'
import { defaultTeamFor, teamHandleInPath, useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { usePageTitle } from '@/composables/usePageTitle'
import { useUnreadNotifications } from '@/composables/useUnreadNotifications'
import { useWorkspaceLayout } from '@/composables/useWorkspaceLayout'

import ConsentGate from './components/account/ConsentGate.vue'
import MyApp from './components/common/MyApp.vue'
import BottomAppBar from './components/common/Navigation/BottomAppBar.vue'
import { railItems, shortcutTarget, tabItems, workspaceProject } from './components/common/Navigation/destinations'
import LeftAppRail from './components/common/Navigation/LeftAppRail.vue'
import { DEFAULT_SHELL, shellFor, termParams } from './lib/shell'
import { usePageTitleStore } from './stores/title'

import { createProject, listProjects } from '@/api'
import { defineCommands } from '@/commands'
import { copyLink, linkOf } from '@/commands/copy'
import CommandPalette from '@/commands/palette/CommandPalette.vue'
import { installShortcuts } from '@/commands/shortcuts'
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AppBar from '@/components/common/Navigation/AppBar.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'
import OfflineBanner from '@/components/common/OfflineBanner.vue'
import VersionBadge from '@/components/common/VersionBadge.vue'
import LeaveProjectDialog from '@/components/LeaveProjectDialog.vue'
import ResourceLimitsNotice from '@/components/ResourceLimitsNotice.vue'
import { t } from '@/i18n'
import { autoConnectThisComputer } from '@/lib/desktop'
import {
  desktopBadge,
  desktopListenForNotices,
  desktopStopNotices,
  inDesktopApp,
  onDesktopOpenPage,
  tellDesktopTheme,
} from '@/lib/desktopApp'
import { landBootSplash } from '@/lib/desktopSplash'
import { trackKeyboardInset } from '@/lib/keyboardInset'
import { pageMotion } from '@/lib/pageMotion'
import { randomTeammateName } from '@/lib/projectAgents'
import { loadCachedProjects, saveCachedProjects } from '@/lib/projectCache'
import {
  applyProjectOrder,
  type DropEdge,
  loadProjectOrder,
  reorderProjects,
  saveProjectOrder,
} from '@/lib/projectOrder'
import { myHandle } from '@/me'
import { NotificationsApi } from '@/network/api/notifications'
import { TeamsApi } from '@/network/api/teams'
import AccountService from '@/services/account'
import { lastOpenedProjectId, useWorkspaceStore } from '@/stores/workspace'
import { useAppTheme } from '@/theme'
import TaskInheritance from '@/views/tasks/components/TaskInheritance.vue'
import { useTaskInheritance } from '@/views/tasks/composables/useTaskInheritance'

// Activate the theme runtime app-wide. First paint is already correct without
// this (the boot script in index.html stamps <html data-theme>, and Vuetify
// boots on the same resolved value), but the OS-preference LISTENER lives in
// this composable — and the only other caller, ThemeToggle, sits inside the
// logged-in user menu. Without this line a signed-out visitor sitting on the
// login page would not follow their machine switching to dark at sunset.
const appTheme = useAppTheme()
// The desktop app paints its first page and its title bar in the same theme.
watch(appTheme.preference, tellDesktopTheme, { immediate: true })

// 软键盘盖住多少，写进 --keyboard-inset 供布局减掉 (style.css)。
trackKeyboardInset()

const currentRoute = useRoute()
const router = useRouter()
const workspace = useWorkspaceStore()

const titleManager = usePageTitle()
const store = usePageTitleStore()

router.isReady().then(async () => {
  const updateDocumentTitle = () => {
    nextTick(() => {
      document.title = titleManager.fullTitle.value
    })
  }

  watch(() => router.currentRoute.value.path, updateDocumentTitle, { immediate: true })
  watch(() => store.updateTrigger, updateDocumentTitle)
  watch(titleManager.fullTitle, updateDocumentTitle)
  watch(() => store.separator, updateDocumentTitle)
})

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
router.afterEach((to, from, failure) => {
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
function endPageMotion(event: AnimationEvent) {
  if (event.target === contentRef.value) contentRef.value?.classList.remove(...MOTION_CLASSES)
}

// 名字来自各自组件里的 defineOptions({ name })——它们也是唯一接了
// useCachedResource 的五个页面，「组件还在」和「数据还在」必须成对，不然回到页
// 面看到的是一屏永远不再刷新的旧数据。
const keptAlivePages = ['ProjectDocsView', 'ProfileView']

// 确认身份的弹窗第一次被要用时才加载：大多数会话从不需要它
const SudoDialog = defineAsyncComponent(() => import('./components/account/SudoDialog.vue'))
const inApp = inDesktopApp()
const DesktopAboutDialog = defineAsyncComponent(() => import('./components/common/DesktopAboutDialog.vue'))
const DesktopPhoneDialog = defineAsyncComponent(() => import('./components/common/DesktopPhoneDialog.vue'))
const sudoWanted = ref(false)
watch(pendingSudo, (request) => {
  if (request) sudoWanted.value = true
})

const hideAppBar = computed(() => {
  return currentRoute.meta.hideAppBar
})

// 页面栈的末端（话题页、私聊页）收起底栏：它们是栈里的一层，不是一级目的地。
const hideTabs = computed(() => currentRoute.meta.hideTabs === true)
/** 这一页有没有侧栏：侧栏是路由上的 `sidebar` 命名视图，渲染它的是上面那个 router-view。 */
const hasSidebar = computed(() => currentRoute.matched.some((record) => record.components?.sidebar))

// Fusion merge (C): 项目来自我们的后端 (/api/projects)，在桌面 rail 上一个项目
// 一格方头像（Discord 式，取代了原来的元思助手），点开的是我们的完整工作区
// (话题/群聊/doc/agent)。两端各拿到哪些格子由 Navigation/destinations.ts 说了算。
const cxProjects = ref<Project[]>(loadCachedProjects(myHandle()))

// 服务端那份清单是 created_at desc，rail 画的是这个人自己拖出来的顺序。两者分开
// 存：拖过之后再刷新项目列表，排法不会被服务端的顺序盖掉。
const projectOrder = ref<string[]>(loadProjectOrder(myHandle()))
const railProjects = computed(() => applyProjectOrder(cxProjects.value, projectOrder.value))

function reorderRail(movedId: string, targetId: string, edge: DropEdge) {
  const next = reorderProjects(railProjects.value, movedId, targetId, edge)
  projectOrder.value = next
  saveProjectOrder(myHandle(), next)
}

const projectListWarning = ref('')
const showProjectListWarning = ref(false)

async function loadCxProjects() {
  // Public visitors have no project list; a 401 here would interrupt the landing page.
  if (!AccountService.loggedIn) {
    cxProjects.value = []
    showProjectListWarning.value = false
    return
  }
  try {
    cxProjects.value = (await listProjects()).data
    saveCachedProjects(myHandle(), cxProjects.value)
    showProjectListWarning.value = false
  } catch {
    projectListWarning.value = cxProjects.value.length ? t('work.projectList.stale') : t('work.projectList.unavailable')
    showProjectListWarning.value = true
  }
}
onMounted(loadCxProjects)

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
    if (id && !cxProjects.value.some((p) => p.id === id)) void loadCxProjects()
  }
)

// The workspace store reads the same list after something changed it from inside
// a project — archived, unarchived, handed over. Take its answer instead of
// showing the rail's older copy until the next reload.
watch(
  () => workspace.projects,
  (list) => {
    if (!workspace.projectsSettled) return
    cxProjects.value = list
    saveCachedProjects(myHandle(), list)
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
    void loadCxProjects()
  }
)

// In the desktop app, being signed in is what makes this computer one of your
// devices: at launch with a session, and at every sign-in (lib/desktop.ts).
watch(
  () => AccountService.loggedIn && AccountService.user?.id,
  (userId) => {
    if (typeof userId === 'number') void autoConnectThisComputer(userId)
  },
  { immediate: true }
)

// The desktop app calls the person back while its window is closed: once handed
// a credential it keeps its own connection for this account's notices, and it
// shows the count of things waiting on its icon. A clicked notification or the
// tray menu opens its page here.
watch(
  () => AccountService.loggedIn && AccountService.user?.id,
  (userId, previous) => {
    if (typeof userId === 'number') {
      desktopListenForNotices(userId, async () => (await NotificationsApi.liveToken()).data.token).catch(() => {})
    } else if (typeof previous === 'number') {
      desktopStopNotices()
    }
  },
  { immediate: true }
)
const stopOpeningPages = onDesktopOpenPage((path) => void router.push(path))
onBeforeUnmount(stopOpeningPages)

// 上次开过的那个项目存在 workspace store 的布局里，所以冷启动也落得回去。
const workspaceProjectId = computed<string | null>(() =>
  workspaceProject(railProjects.value, workspace.projectId, lastOpenedProjectId())
)

// 待我处理的件数：桌面画在首页那一格上，手机画在底栏「待办」上。
const awaitingCount = useAwaitingCount(computed(() => AccountService._loggedIn.value))
// 没读的动态（提到你、回复你……）：没有待处理的事时，同一格上画一颗小点。
const { count: unreadActivity } = useUnreadNotifications()
// The same number on the desktop app's icon, whenever this page has read it.
watch(awaitingCount, desktopBadge)

// 右键 rail 上一个项目：复制链接、打开项目设置，不是所有者的还能退出。都是别处已有
// 的操作——项目菜单、成员页——这里只是把它们挂到那一格上，对的是那一格的项目，
// 不一定是正开着的这个。
const leaveOpen = ref(false)
const leavingProjectId = ref<string | null>(null)
function projectMenu(project: Project): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'project.copyLink',
      label: t('work.room.menu.copyLink'),
      icon: 'mdi-link-variant',
      onSelect: () => void copyLink(linkOf(router, { name: 'workspace-project', params: { projectId: project.id } })),
    },
    {
      key: 'project.settings',
      label: t('work.projectSettings.title'),
      icon: 'mdi-cog-outline',
      onSelect: () => void router.push({ name: 'project-settings', params: { projectId: project.id } }),
    },
  ]
  if (project.owner_handle !== myHandle())
    actions.push({
      key: 'project.leave',
      label: t('work.members.leave'),
      icon: 'mdi-exit-to-app',
      danger: true,
      onSelect: () => {
        leavingProjectId.value = project.id
        leaveOpen.value = true
      },
    })
  return actions
}

const navSources = computed<NavSources>(() => ({
  projects: railProjects.value,
  workspaceProjectId: workspaceProjectId.value,
  projectAvatar,
  createProject: createNewProject,
  projectMenu,
  awaitingCount: awaitingCount.value,
  unreadActivity: unreadActivity.value > 0,
}))

// 壳 (shell)：**地址里那个项目**的壳决定这份导航怎么画。不在项目里（首页、空间、
// 设置、某个 赛题 页）时是 default——那里没有项目行可读，而 default 就是今天的
// 样子，所以项目外的一点都没变。按地址取而不是按「上次开过的项目」取：壳是**你
// 现在待的地方**的长相，走出项目还挂着上一个项目的样子会让人以为走岔了。
const openProjectId = computed<string | null>(() =>
  typeof currentRoute.params.projectId === 'string' ? currentRoute.params.projectId : null
)
const navShell = computed(() => shellFor(railProjects.value, openProjectId.value) ?? DEFAULT_SHELL)

const rail = computed(() => railItems(navSources.value, navShell.value))

// rail 的悬停浮层一直在说 ⌘N 能切过去；这里是它真正被绑上的地方。只有真的对上
// 某一格的数字才登记，对不上的照旧归浏览器——项目只有三个的时候 ⌘7 仍然切你的第
// 七个标签页。
defineCommands(() =>
  [1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((digit) => {
    const to = shortcutTarget(rail.value, digit)
    const item = rail.value.find((it) => it.type === 'item' && it.to === to)
    const title = item?.type === 'item' ? item.title : to
    return to
      ? [{ id: `rail.${digit}`, title: title ?? to, shortcut: `mod+${digit}`, to, palette: false as const }]
      : []
  })
)
let stopShortcuts: (() => void) | undefined
onMounted(() => (stopShortcuts = installShortcuts(router)))
onBeforeUnmount(() => stopShortcuts?.())
const tabs = computed(() => tabItems(navSources.value, navShell.value))
// The "+" rail affordance opens an in-app dialog (no native prompt). On confirm
// we create the project owned by the current user, refresh the rail so the new
// tile appears, then open its workspace. The same dialog is what a team page's
// 新建项目 opens (useNewProjectDialog), with that team preselected.
const { open: newProjectDialog, presetTeam, sourceTask, show: showNewProjectDialog } = useNewProjectDialog()
// 从一道题建项目时那份「会继承什么」(#944)。取数按 `sourceTask` 走：对话框换个
// 来源就重新问一次，没来源时不发请求。
const { inheritance: sourceInheritance, loading: sourceInheritanceLoading } = useTaskInheritance(
  () => sourceTask.value?.id
)
const newProjectName = ref('')
const newProjectStep = ref(1)
const newProjectAgentName = ref('')
const newProjectForgeKind = ref<'forgejo' | 'github_app'>('forgejo')
// 这个项目打算做什么——建项目时问的那一句（#946 片 C）。问题只在人愿意答的时候
// 才有价值，所以它是可选的：空着就和从前一样，只建一个空房间。
const newProjectIntent = ref('')
// The project's id, chosen once per opening of the dialog. Pressing 创建项目 again
// after a failure sends the same id, so the server finishes that project rather
// than starting another beside what the failed attempt already made.
const newProjectId = ref('')
const creatingProject = ref(false)
const newProjectError = ref<string | null>(null)
// 归属: which team the project belongs to decides who can see it. Without
// this the rail ＋ always chose the personal team, so a project someone made
// for their team was invisible to the rest of it.
const newProjectTeams = ref<Team[]>([])
const newProjectTeamId = ref<number | null>(null)
const loadingTeams = ref(false)
const teamLoadError = ref<string | null>(null)
// 自己名下以自己的昵称出现（后端给的 name 就是昵称），和团队的区别写在下面那行小字里。
const teamItemProps = (team: Team) => ({
  subtitle: team.personal
    ? t('work.newProject.ownHint')
    : t('work.newProject.teamHint', { n: 1 + (team.admins?.total ?? 0) + (team.members?.total ?? 0) }),
})

function createNewProject() {
  // From a team page, that team; elsewhere the dialog falls back to the caller's own name.
  showNewProjectDialog(teamHandleInPath(currentRoute.path))
}

// 不属于哪一页、在哪都能做的事：命令面板的「操作」里有它们。
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

async function loadProjectTeams() {
  loadingTeams.value = true
  teamLoadError.value = null
  newProjectTeamId.value = null
  try {
    const {
      data: { teams },
    } = await TeamsApi.getMyTeams()
    newProjectTeams.value = teams
    if (!teams.length) teamLoadError.value = t('work.newProject.teamsEmpty')
  } catch {
    newProjectTeams.value = []
    teamLoadError.value = t('work.newProject.teamsFailed')
  } finally {
    loadingTeams.value = false
  }
  newProjectTeamId.value = defaultTeamFor(presetTeam.value, newProjectTeams.value)
}

watch(newProjectDialog, (opened) => {
  if (!opened) return
  newProjectName.value = sourceTask.value?.name ?? ''
  newProjectStep.value = 1
  newProjectAgentName.value = randomTeammateName()
  newProjectForgeKind.value = 'forgejo'
  // 上一次开的对话框留下的答案不该跟到这一次——那会让第二个项目凭空继承第一个
  // 项目的说明，而人根本没说过。
  newProjectIntent.value = ''
  newProjectId.value = crypto.randomUUID()
  newProjectError.value = null
  void loadProjectTeams()
})

function advanceNewProject() {
  if (newProjectName.value.trim() && !loadingTeams.value && !teamLoadError.value && newProjectTeamId.value !== null)
    newProjectStep.value = 2
}

async function confirmNewProject() {
  if (newProjectStep.value !== 2 || !newProjectAgentName.value.trim()) return
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value || loadingTeams.value || teamLoadError.value || newProjectTeamId.value === null)
    return
  creatingProject.value = true
  newProjectError.value = null
  try {
    const project = await createProject(
      name,
      newProjectTeamId.value,
      sourceTask.value?.id,
      newProjectForgeKind.value,
      newProjectIntent.value.trim(),
      newProjectAgentName.value.trim(),
      newProjectId.value
    )
    await loadCxProjects()
    newProjectDialog.value = false
    if (newProjectForgeKind.value === 'github_app') {
      router.push(`/projects/${project.id}/settings/repository`)
      return
    }
    // 直接落到大本营，而不是项目地址。一个刚建出来的项目没有任何活，而 /projects
    // 的落点是看板——它此刻是四列空格子，答的是「什么在跑」，对一个还没开始的项目
    // 只有一个答案：没有。人第一眼该看到的是能说话的地方。这里知道它是新的，所以
    // 不用等话题列表回来才推断（WorkspaceEntry 负责那种情况）。
    router.push(
      project.root_topic_id ? `/projects/${project.id}/topics/${project.root_topic_id}` : `/projects/${project.id}`
    )
  } catch (e) {
    // Inline error inside the dialog — not a native alert() chrome.
    newProjectError.value = e instanceof Error ? e.message : t('work.newProject.createFailed')
  } finally {
    creatingProject.value = false
  }
}

// 项目格子：首字压在 avatarColor() 的底色上，和人的默认头像同一套取色——
// 色相由名字散列而来，明度固定，所以每一种色相上的白字都过 4.5:1。白字写死是
// 对的：底色本身不随主题变，字也不能变。
function projectAvatar(name: string): string {
  const trimmed = (name || '').trim()
  const ch = trimmed ? [...trimmed][0] : '·'
  const esc = ch.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">` +
    `<rect width="48" height="48" fill="${avatarColor(trimmed)}"/>` +
    `<text x="24" y="24" font-size="24" fill="#ffffff" text-anchor="middle" ` +
    `dominant-baseline="central" font-family="sans-serif" font-weight="700">${esc}</text></svg>`
  // Unicode-safe base64 (the initial may be CJK) — more robust in v-img than a
  // percent-encoded data URI.
  const b64 = btoa(encodeURIComponent(svg).replace(/%([0-9A-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16))))
  return `data:image/svg+xml;base64,${b64}`
}
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
  top: 10px;
  left: 16px;
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
