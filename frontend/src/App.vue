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
  </my-app>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { listProjects } from '@/api'
import type { Project } from '@/cx_types'
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
  { key: 'Home', type: 'item', title: '首页', to: '/', icon: 'cheese', visibleOnMobile: false },
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
  ...cxProjects.value.map((p) => ({
    key: `cx-${p.id}`,
    type: 'item' as const,
    title: p.name,
    to: `/cxproject/${p.id}`,
    img: projectAvatar(p.name),
  })),
])

function projectAvatar(name: string): string {
  const trimmed = (name || '').trim()
  const ch = trimmed ? [...trimmed][0] : '·'
  const colors = ['#F57F17', '#1f9d55', '#2563eb', '#7c3aed', '#dc2626', '#0891b2']
  const c = colors[trimmed.length % colors.length]
  const esc = ch.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">` +
    `<rect width="48" height="48" rx="14" fill="${c}"/>` +
    `<text x="24" y="24" font-size="22" fill="#ffffff" text-anchor="middle" ` +
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
  overscroll-behavior: contain;
}
</style>
