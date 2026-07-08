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

    <!-- The one app-global 现场 (live agent screen) viewer, opened by any agent avatar. -->
    <AgentSceneDialog />
  </my-app>
</template>

<script setup lang="ts">
import { computed, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useRouter } from 'vue-router'

import { useExperimental } from '@/composables/useExperimental'
import { usePageTitle } from '@/composables/usePageTitle'

import AgentSceneDialog from './components/common/AgentSceneDialog.vue'
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

const experimental = useExperimental()

const navItems = computed<NavGenericItem[]>(() => {
  const items: NavGenericItem[] = [
    {
      key: 'Home',
      type: 'item',
      title: '首页',
      to: '/',
      icon: 'cheese',
      visibleOnMobile: false,
    },
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
  ]
  // 非内测态下才显示「元思」入口；内测态下「元思」已不再需要。
  if (!experimental.value) {
    items.push({
      key: 'Assistant',
      type: 'item',
      title: '元思',
      to: '/assistant',
      icon: 'mdi-assistant',
    })
  }
  // 内测态下，主滑轨上多出「聊天」入口（全局、与项目无关；未来文档、待办也挂到这条滑轨上）。
  if (experimental.value) {
    items.push({
      key: 'Chat',
      type: 'item',
      title: '聊天',
      to: '/chat?exp=true',
      icon: 'mdi-forum-outline',
    })
    items.push({
      key: 'ProjectDocs',
      type: 'item',
      title: '项目',
      to: '/documents?exp=true',
      icon: 'mdi-file-tree-outline',
    })
  }
  return items
})
</script>

<style lang="scss" scoped>
.app-content {
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
}
</style>
