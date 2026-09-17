<template>
  <!-- `background` (not a fixed grey): the title bar frames every page, so a
       Material palette name like grey-lighten-5 would pin it to #FAFAFA in dark
       theme while the text inside follows --v-theme-on-surface → unreadable. -->
  <v-system-bar window color="background" absolute class="app-system-bar">
    <ParentBackButton />
    <div class="text-caption font-weight-bold title-bar flex-grow-1">
      <span class="text-caption">{{ currentTitle }}</span>
    </div>
    <div class="position-relative d-flex align-center justify-center">
      <v-spacer></v-spacer>
      <LanguageToggle />
      <v-menu
        v-if="loggedIn"
        v-model="notificationMenuOpen"
        :close-on-content-click="false"
        location="bottom"
        :offset="16"
        transition="scale-transition"
      >
        <template #activator="{ props }">
          <v-btn
            icon
            position="relative"
            v-bind="props"
            color="on-surface-variant"
            :size="28"
            variant="text"
            aria-label="通知"
            title="通知"
          >
            <v-icon size="18">mdi-bell</v-icon>
            <v-badge
              v-if="unreadNotificationsCount > 0"
              color="error"
              :content="unreadNotificationsCount > 99 ? '99+' : unreadNotificationsCount.toString()"
              floating
              dot
              :model-value="unreadNotificationsCount > 0"
            ></v-badge>
          </v-btn>
        </template>
        <notification-panel @update-count="updateUnreadCount" />
      </v-menu>
      <v-btn v-else icon :size="28" variant="text" color="on-surface-variant" aria-label="通知" disabled>
        <v-icon size="18">mdi-bell</v-icon>
      </v-btn>
    </div>
  </v-system-bar>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { usePageTitle } from '@/composables/usePageTitle'

import NotificationPanel from '../Notification/NotificationPanel.vue'

import ParentBackButton from './ParentBackButton.vue'

import LanguageToggle from '@/components/common/LanguageToggle.vue'
import { t } from '@/i18n'
import { NotificationsApi } from '@/network/api/notifications'
import AccountService from '@/services/account'
import { usePageTitleStore } from '@/stores/title'

const router = useRouter()
const { updateTrigger } = usePageTitleStore()
const { getRouteHierarchy } = usePageTitle()

const notificationMenuOpen = ref(false)
const unreadNotificationsCount = ref(0)

const currentTitle = ref(t('global.cheese2'))

const updateTitle = () => {
  const hierarchy = getRouteHierarchy.value
  for (const item of hierarchy) {
    if (item.meta.isFullPage) {
      currentTitle.value = item.title
      return
    }
  }
  currentTitle.value = t('global.cheese2')
}

watch([getRouteHierarchy, () => updateTrigger], updateTitle, { immediate: true })

watch(
  () => router.currentRoute.value.fullPath,
  () => {
    notificationMenuOpen.value = false
  }
)

const loggedIn = computed(() => AccountService._loggedIn.value)

// 获取未读通知数量
const fetchUnreadNotificationsCount = async () => {
  if (!loggedIn.value) return

  try {
    const response = await NotificationsApi.getUnreadCount()
    unreadNotificationsCount.value = response.data.count
  } catch (error) {
    console.error('获取未读通知数量失败:', error)
  }
}

// 更新未读通知数量
const updateUnreadCount = (count: number) => {
  unreadNotificationsCount.value = count
}

watch(loggedIn, (newValue) => {
  if (newValue) {
    fetchUnreadNotificationsCount()
  } else {
    unreadNotificationsCount.value = 0
  }
})

onMounted(() => {
  if (loggedIn.value) {
    fetchUnreadNotificationsCount()
  }
})
</script>

<style>
.app-bar-title {
  user-select: none;
}

.title-bar {
  height: 100%;
  user-select: none;
  display: flex;
  justify-content: center;
  align-items: center;
}

/* VSystemBar dims the WHOLE bar with `opacity: var(--v-medium-emphasis-opacity)`,
   which lands the 12px title at 0.62 ink over the canvas — #7f8184 on #f7f8fa,
   3.68:1, under the 4.5:1 AA needs for small text. (Pre-existing: it measured
   3.70:1 on the old grey-lighten-5 too.) Opacity is the wrong tool anyway — it
   fades the bar's own background as well. Restore it and reach for the token
   that MEANS "secondary text", `on-surface-variant` (= --muted): 4.81:1 light,
   7.11:1 dark, and it is a real colour rather than a fade. */
.app-system-bar.app-system-bar {
  opacity: 1;
  color: rgb(var(--v-theme-on-surface-variant));
}

.app-system-bar .language-toggle {
  min-height: 24px;
  padding: 2px 8px;
  font-size: 12px;
}

.floating-search-container {
  float: left;
}

.user-menu-card {
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.user-menu-list .v-list-item {
  transition: all 0.2s ease;
  min-height: 44px;
}

.user-menu-list .v-list-item:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
}

.cursor-pointer {
  cursor: pointer;
}

.primary-gradient {
  background: linear-gradient(135deg, var(--v-theme-primary), var(--v-theme-primary-darken-1));
}

.ai-quota-card {
  transition: all 0.2s ease;
  overflow: hidden;
}
</style>
