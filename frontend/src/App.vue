<template>
  <router-view v-if="currentRoute.meta.publicLanding" />
  <my-app v-else>
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

      <!-- 一级导航：桌面端左侧 Rail，移动端底部 -->
      <keep-alive>
        <BottomAppBar v-if="!hideAppBar && !hideTabs" :items="tabs" />
      </keep-alive>
    </template>

    <v-main class="bg-background h-100">
      <div class="border-t-sm bg-background h-100 overflow-hidden">
        <div id="app-scrollable" class="app-content h-100">
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

    <!-- 新建项目 dialog (opened by the rail's "+" affordance) -->
    <v-dialog v-model="newProjectDialog" max-width="420" persistent>
      <v-card rounded="lg" class="pa-2">
        <v-card-title class="text-h6 font-weight-bold pb-1">新建项目</v-card-title>
        <v-card-text class="pb-2">
          <p v-if="sourceTask" class="t-body c-muted mb-3">来自赛题：{{ sourceTask.name }}</p>
          <ResourceLimitsNotice v-if="newProjectDialog" />
          <v-text-field
            v-model="newProjectName"
            autocomplete="off"
            label="项目名称"
            variant="outlined"
            color="primary"
            autofocus
            hide-details
            :disabled="creatingProject"
            @keyup.enter="confirmNewProject"
          />
          <v-select
            v-model="newProjectTeamId"
            autocomplete="off"
            :items="newProjectTeams"
            :item-title="teamLabel"
            item-value="id"
            label="所属团队"
            variant="outlined"
            color="primary"
            class="mt-3"
            hide-details
            :loading="loadingTeams"
            :disabled="creatingProject || loadingTeams"
          />
          <div class="t-meta mt-2">项目归所选团队，成员可以一起协作</div>
          <v-alert v-if="teamLoadError" type="error" density="compact" variant="tonal" class="mt-3">
            {{ teamLoadError }}
            <v-btn variant="text" size="small" :loading="loadingTeams" @click="loadProjectTeams">重试</v-btn>
          </v-alert>
          <v-alert v-if="newProjectError" type="error" density="compact" variant="tonal" class="mt-3">
            {{ newProjectError }}
          </v-alert>
        </v-card-text>
        <v-card-actions class="px-4 pb-3">
          <v-spacer />
          <v-btn variant="text" :disabled="creatingProject" @click="newProjectDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creatingProject"
            :disabled="!newProjectName.trim() || loadingTeams || newProjectTeamId === null || !!teamLoadError"
            @click="confirmNewProject"
          >
            创建
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="showProjectListWarning" color="warning" :timeout="8000">
      {{ projectListWarning }}
      <template #actions>
        <v-btn variant="text" @click="loadCxProjects">重试</v-btn>
      </template>
    </v-snackbar>

    <!-- 内测: running-build badge, self-hides unless the box opted in. -->
    <VersionBadge />

    <!-- 离线指示: shows only while offline, auto-hides when the network returns. -->
    <OfflineBanner />

    <!-- 有新版本: shows while a new service worker waits for the user to click. -->
    <UpdateBanner />
  </my-app>
</template>

<script setup lang="ts">
import type { Project } from '@/cx_types'
import type { Team } from '@/types/teams'
import type { NavSources } from './components/common/Navigation/destinations'

import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { useEventListener } from '@vueuse/core'

import { defaultTeamFor, teamIdInPath, useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { usePageTitle } from '@/composables/usePageTitle'

import MyApp from './components/common/MyApp.vue'
import BottomAppBar from './components/common/Navigation/BottomAppBar.vue'
import { railItems, shortcutTarget, tabItems, workspaceProject } from './components/common/Navigation/destinations'
import LeftAppRail from './components/common/Navigation/LeftAppRail.vue'
import { DEFAULT_SHELL, shellFor } from './lib/shell'
import { usePageTitleStore } from './stores/title'

import { createProject, listProjects } from '@/api'
import AppBar from '@/components/common/Navigation/AppBar.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'
import OfflineBanner from '@/components/common/OfflineBanner.vue'
import UpdateBanner from '@/components/common/UpdateBanner.vue'
import VersionBadge from '@/components/common/VersionBadge.vue'
import ResourceLimitsNotice from '@/components/ResourceLimitsNotice.vue'
import { trackKeyboardInset } from '@/lib/keyboardInset'
import { loadCachedProjects, saveCachedProjects } from '@/lib/projectCache'
import {
  applyProjectOrder,
  type DropEdge,
  loadProjectOrder,
  reorderProjects,
  saveProjectOrder,
} from '@/lib/projectOrder'
import { myHandle } from '@/me'
import { TeamsApi } from '@/network/api/teams'
import AccountService from '@/services/account'
import { lastOpenedProjectId, useWorkspaceStore } from '@/stores/workspace'
import { useAppTheme } from '@/theme'

// Activate the theme runtime app-wide. First paint is already correct without
// this (the boot script in index.html stamps <html data-theme>, and Vuetify
// boots on the same resolved value), but the OS-preference LISTENER lives in
// this composable — and the only other caller, ThemeToggle, sits inside the
// logged-in user menu. Without this line a signed-out visitor sitting on the
// login page would not follow their machine switching to dark at sunset.
useAppTheme()

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
  watch([() => store.siteName, () => store.separator], updateDocumentTitle)
})

// 名字来自各自组件里的 defineOptions({ name })——它们也是唯一接了
// useCachedResource 的五个页面，「组件还在」和「数据还在」必须成对，不然回到页
// 面看到的是一屏永远不再刷新的旧数据。
const keptAlivePages = ['OverviewView', 'ProjectDocsView', 'MemberView', 'CalendarView', 'ProjectAgentsView']

const hideAppBar = computed(() => {
  return currentRoute.meta.hideAppBar
})

// 页面栈的末端（话题页、私聊页）收起底栏：它们是栈里的一层，不是一级目的地。
const hideTabs = computed(() => currentRoute.meta.hideTabs === true)

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
    projectListWarning.value = cxProjects.value.length
      ? '项目列表刷新失败，正在显示上次成功加载的内容'
      : '项目列表暂时无法加载，请稍后重试'
    showProjectListWarning.value = true
  }
}
onMounted(loadCxProjects)

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

// 上次开过的那个项目存在 workspace store 的布局里，所以冷启动也落得回去。
const workspaceProjectId = computed<string | null>(() =>
  workspaceProject(railProjects.value, workspace.projectId, lastOpenedProjectId())
)

const navSources = computed<NavSources>(() => ({
  projects: railProjects.value,
  workspaceProjectId: workspaceProjectId.value,
  projectAvatar,
  createProject: createNewProject,
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

// rail 的悬停浮层一直在说 ⌘N 能切过去；这里是它真正被绑上的地方。
//
// 这是从浏览器手里**抢**来的：⌘1–9 本来是切标签页，和 Slack 网页版一样的取舍。
// 所以只在这个数字真的对上某一格时才拦下来，对不上的照旧交回给浏览器——项目只有
// 三个的时候 ⌘7 仍然切你的第七个标签页。
//
// 认 `code` 不认 `key`：`key` 跟着键盘布局走，法语 AZERTY 上不按 Shift 的那一排
// 根本不是数字，而人看着的是同一个物理键。
useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return
  const digit = /^Digit([1-9])$/.exec(event.code)
  if (!digit) return
  const to = shortcutTarget(rail.value, Number(digit[1]))
  if (!to) return
  event.preventDefault()
  void router.push(to)
})
const tabs = computed(() => tabItems(navSources.value, navShell.value))
// The "+" rail affordance opens an in-app dialog (no native prompt). On confirm
// we create the project owned by the current user, refresh the rail so the new
// tile appears, then open its workspace. The same dialog is what a team page's
// 新建项目 opens (useNewProjectDialog), with that team preselected.
const { open: newProjectDialog, presetTeamId, sourceTask, show: showNewProjectDialog } = useNewProjectDialog()
const newProjectName = ref('')
const creatingProject = ref(false)
const newProjectError = ref<string | null>(null)
// 所属小队: which team the project belongs to decides who can see it. Without
// this the rail ＋ always chose the personal team, so a project someone made
// for their team was invisible to the rest of it.
const newProjectTeams = ref<Team[]>([])
const newProjectTeamId = ref<number | null>(null)
const loadingTeams = ref(false)
const teamLoadError = ref<string | null>(null)
const teamLabel = (t: Team) => (t.personal ? '个人' : t.name)

function createNewProject() {
  // From a team page, that team; elsewhere the dialog falls back to 个人.
  showNewProjectDialog(teamIdInPath(currentRoute.path))
}

async function loadProjectTeams() {
  loadingTeams.value = true
  teamLoadError.value = null
  newProjectTeamId.value = null
  try {
    const {
      data: { teams },
    } = await TeamsApi.getMyTeams()
    newProjectTeams.value = teams
    if (!teams.length) teamLoadError.value = '暂无可用团队，请先创建或加入团队'
  } catch {
    newProjectTeams.value = []
    teamLoadError.value = '团队列表加载失败，请重试后选择项目归属'
  } finally {
    loadingTeams.value = false
  }
  newProjectTeamId.value = defaultTeamFor(presetTeamId.value, newProjectTeams.value)
}

watch(newProjectDialog, (opened) => {
  if (!opened) return
  newProjectName.value = sourceTask.value?.name ?? ''
  newProjectError.value = null
  void loadProjectTeams()
})

async function confirmNewProject() {
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value || loadingTeams.value || teamLoadError.value || newProjectTeamId.value === null)
    return
  creatingProject.value = true
  newProjectError.value = null
  try {
    const project = await createProject(name, myHandle(), newProjectTeamId.value, sourceTask.value?.id)
    await loadCxProjects()
    newProjectDialog.value = false
    // 直接落到大本营，而不是项目地址。一个刚建出来的项目没有任何活，而 /projects
    // 的落点是看板——它此刻是四列空格子，答的是「什么在跑」，对一个还没开始的项目
    // 只有一个答案：没有。人第一眼该看到的是能说话的地方。这里知道它是新的，所以
    // 不用等话题列表回来才推断（WorkspaceEntry 负责那种情况）。
    router.push(
      project.root_topic_id ? `/projects/${project.id}/topics/${project.root_topic_id}` : `/projects/${project.id}`
    )
  } catch (e) {
    // Inline error inside the dialog — not a native alert() chrome.
    newProjectError.value = e instanceof Error ? e.message : '创建项目失败'
  } finally {
    creatingProject.value = false
  }
}

// Discord-style ⌘N quick-switch: ⌘1 首页, ⌘2.. projects.
function onRailShortcut(e: KeyboardEvent) {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return
  const n = Number(e.key)
  if (!n) return
  const item = rail.value.find((it) => it.type === 'item' && it.shortcut === n)
  if (item && item.type === 'item' && item.to) {
    e.preventDefault()
    router.push(item.to)
  }
}
onMounted(() => window.addEventListener('keydown', onRailShortcut))

function projectAvatar(name: string): string {
  const trimmed = (name || '').trim()
  const ch = trimmed ? [...trimmed][0] : '·'
  const colors = ['#F57F17', '#1f9d55', '#2563eb', '#7c3aed', '#dc2626', '#0891b2']
  const c = colors[trimmed.length % colors.length]
  const esc = ch.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">` +
    `<rect width="48" height="48" rx="11" fill="${c}"/>` +
    `<text x="24" y="24" font-size="24" fill="#ffffff" text-anchor="middle" ` +
    `dominant-baseline="central" font-family="sans-serif" font-weight="700">${esc}</text></svg>`
  // Unicode-safe base64 (the initial may be CJK) — more robust in v-img than a
  // percent-encoded data URI.
  const b64 = btoa(encodeURIComponent(svg).replace(/%([0-9A-F]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16))))
  return `data:image/svg+xml;base64,${b64}`
}
</script>

<style lang="scss" scoped>
.app-content {
  min-height: 0;
  overflow: auto;
  /* contain only the vertical axis: `contain` on both axes also swallows the
     browser's horizontal swipe-to-go-back gesture (fusion: 左划返回失效). */
  overscroll-behavior-y: contain;
}
</style>
