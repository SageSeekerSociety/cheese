<template>
  <my-app>
    <!-- 桌面端：使用 StatusBar -->
    <template v-if="$vuetify.display.mdAndUp">
      <keep-alive>
        <app-bar v-if="!hideAppBar" :links="[]" />
      </keep-alive>
      <keep-alive>
        <LeftAppRail v-if="!hideAppBar" :items="rail" />
      </keep-alive>

      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />
    </template>
    <template v-else>
      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />

      <!-- 移动端：使用全高 Toolbar -->
      <keep-alive>
        <mobile-app-bar v-if="!hideAppBar && !ownHeader" />
      </keep-alive>

      <!-- 一级导航：桌面端左侧 Rail，移动端底部 -->
      <keep-alive>
        <BottomAppBar v-if="!hideAppBar && !hideTabs" :items="tabs" />
      </keep-alive>
    </template>

    <v-main class="bg-background h-100">
      <div class="border-t-sm bg-background h-100 overflow-hidden">
        <div id="app-scrollable" class="app-content h-100">
          <router-view />
        </div>
      </div>
    </v-main>

    <!-- 新建项目 dialog (opened by the rail's "+" affordance) -->
    <v-dialog v-model="newProjectDialog" max-width="420" persistent>
      <v-card rounded="lg" class="pa-2">
        <v-card-title class="text-h6 font-weight-bold pb-1">新建项目</v-card-title>
        <v-card-text class="pb-2">
          <v-text-field
            v-model="newProjectName"
            label="项目名称"
            variant="outlined"
            color="primary"
            autofocus
            hide-details
            :disabled="creatingProject"
            @keyup.enter="confirmNewProject"
          />
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
            :disabled="!newProjectName.trim()"
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
  </my-app>
</template>

<script setup lang="ts">
import type { Project } from '@/cx_types'
import type { NavSources } from './components/common/Navigation/destinations'

import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'

import { usePageTitle } from '@/composables/usePageTitle'

import MyApp from './components/common/MyApp.vue'
import BottomAppBar from './components/common/Navigation/BottomAppBar.vue'
import { railItems, tabItems, workspaceProject } from './components/common/Navigation/destinations'
import LeftAppRail from './components/common/Navigation/LeftAppRail.vue'
import { usePageTitleStore } from './stores/title'

import { createProject, listProjects } from '@/api'
import AppBar from '@/components/common/Navigation/AppBar.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'
import OfflineBanner from '@/components/common/OfflineBanner.vue'
import VersionBadge from '@/components/common/VersionBadge.vue'
import { trackKeyboardInset } from '@/lib/keyboardInset'
import { loadCachedProjects, saveCachedProjects } from '@/lib/projectCache'
import { myHandle } from '@/me'
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
  watch([() => store.siteName, () => store.separator], updateDocumentTitle)
})

const hideAppBar = computed(() => {
  return currentRoute.meta.hideAppBar
})

// 页面栈的末端（话题页、私聊页）收起底栏：它们是栈里的一层，不是一级目的地。
const hideTabs = computed(() => currentRoute.meta.hideTabs === true)

// 自带页头的页面不要系统顶栏——两条横条写同一个标题，在手机上很贵。
const ownHeader = computed(() => currentRoute.meta.ownHeader === true)

// Fusion merge (C): 项目来自我们的后端 (/api/projects)，在桌面 rail 上一个项目
// 一格方头像（Discord 式，取代了原来的元思助手），点开的是我们的完整工作区
// (话题/群聊/doc/agent)。两端各拿到哪些格子由 Navigation/destinations.ts 说了算。
const cxProjects = ref<Project[]>(loadCachedProjects(myHandle()))
const projectListWarning = ref('')
const showProjectListWarning = ref(false)

async function loadCxProjects() {
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
    void loadCxProjects()
  }
)

// 上次开过的那个项目存在 workspace store 的布局里，所以冷启动也落得回去。
const workspaceProjectId = computed<string | null>(() =>
  workspaceProject(cxProjects.value, workspace.projectId, lastOpenedProjectId())
)

const navSources = computed<NavSources>(() => ({
  projects: cxProjects.value,
  workspaceProjectId: workspaceProjectId.value,
  projectAvatar,
  createProject: createNewProject,
}))

const rail = computed(() => railItems(navSources.value))
const tabs = computed(() => tabItems(navSources.value))
// The "+" rail affordance opens an in-app dialog (no native prompt). On confirm
// we create the project owned by the current user, refresh the rail so the new
// tile appears, then open its workspace.
const newProjectDialog = ref(false)
const newProjectName = ref('')
const creatingProject = ref(false)
const newProjectError = ref<string | null>(null)

function createNewProject() {
  newProjectName.value = ''
  newProjectError.value = null
  newProjectDialog.value = true
}

async function confirmNewProject() {
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value) return
  creatingProject.value = true
  newProjectError.value = null
  try {
    const project = await createProject(name, myHandle())
    await loadCxProjects()
    newProjectDialog.value = false
    router.push(`/projects/${project.id}`)
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
