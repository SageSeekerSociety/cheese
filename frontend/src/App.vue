<template>
  <my-app>
    <!-- 桌面端：使用 StatusBar -->
    <template v-if="$vuetify.display.mdAndUp">
      <keep-alive>
        <app-bar v-if="!hideAppBar" :links="[]" />
      </keep-alive>
      <keep-alive>
        <LeftAppRail v-if="!hideAppBar" :items="navItems" />
      </keep-alive>

      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />
    </template>
    <template v-else>
      <!-- 二级导航：通过路由渲染 -->
      <router-view name="sidebar" />

      <!-- 移动端：使用全高 Toolbar -->
      <keep-alive>
        <mobile-app-bar v-if="!hideAppBar" />
      </keep-alive>

      <!-- 一级导航：桌面端左侧 Rail，移动端底部 -->
      <keep-alive>
        <BottomAppBar v-if="!hideAppBar" :items="navItems" />
      </keep-alive>
    </template>

    <v-main class="bg-grey-lighten-5 h-100">
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
          <v-alert
            v-if="newProjectError"
            type="error"
            density="compact"
            variant="tonal"
            class="mt-3"
          >
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
  </my-app>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { createProject, listProjects } from '@/api'
import type { Project } from '@/cx_types'
import { myHandle } from '@/me'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'

import { usePageTitle } from '@/composables/usePageTitle'

import MyApp from './components/common/MyApp.vue'
import BottomAppBar from './components/common/Navigation/BottomAppBar.vue'
import LeftAppRail from './components/common/Navigation/LeftAppRail.vue'
import { NavGenericItem } from './components/common/Navigation/types'
import { usePageTitleStore } from './stores/title'

import AppBar from '@/components/common/Navigation/AppBar.vue'
import MobileAppBar from '@/components/common/Navigation/MobileAppBar.vue'

const currentRoute = useRoute()
const router = useRouter()

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

// Fusion merge (C): the LeftAppRail carries the product surfaces (首页/空间/小队)
// PLUS our projects — each project is a rail icon (Discord-style, replacing the
// old 元思 assistant). Clicking a project opens OUR full workspace (topics/群聊/
// doc/agent) for it. Projects come from our backend (/api/projects).
const cxProjects = ref<Project[]>([])

async function loadCxProjects() {
  try {
    cxProjects.value = (await listProjects()).data
  } catch {
    cxProjects.value = []
  }
}
onMounted(loadCxProjects)

const navItems = computed<NavGenericItem[]>(() => [
  { key: 'Home', type: 'item', title: '首页', to: '/', icon: 'cheese', visibleOnMobile: false, shortcut: 1 },
  {
    key: 'Spaces',
    type: 'item',
    title: '空间',
    to: '/spaces',
    icon: 'mdi-view-dashboard',
    visibleOnMobile: true,
    visibleOnPC: false,
  },
  {
    key: 'Teams',
    type: 'item',
    title: '小队',
    to: '/teams',
    icon: 'mdi-account-group',
    visibleOnMobile: true,
    visibleOnPC: false,
  },
  ...(cxProjects.value.length
    ? [{ key: 'cx-divider', type: 'divider' as const }]
    : []),
  // Each project → our workspace (topics/群聊/doc/agent). Discord-style: a
  // squircle avatar (initial + color), not a cut-off title.
  ...cxProjects.value.map((p, i) => ({
    key: `cx-${p.id}`,
    type: 'item' as const,
    title: p.name,
    to: `/project/${p.id}`,
    img: projectAvatar(p.name),
    shortcut: i + 2, // ⌘1 = 首页, then projects
  })),
  // Discord-style "+" at the bottom of the project list: create a new project.
  {
    key: 'cx-add',
    type: 'item' as const,
    title: '新建项目',
    icon: 'mdi-plus',
    add: true,
    action: createNewProject,
    visibleOnMobile: false,
  },
])

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
    router.push(`/project/${project.id}`)
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
  const item = navItems.value.find((it) => it.type === 'item' && it.shortcut === n)
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
  const b64 = btoa(
    encodeURIComponent(svg).replace(/%([0-9A-F]{2})/g, (_, h) =>
      String.fromCharCode(parseInt(h, 16)),
    ),
  )
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
