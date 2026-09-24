<template>
  <!-- `background` (not a fixed grey): the title bar frames every page, so a
       Material palette name like grey-lighten-5 would pin it to #FAFAFA in dark
       theme while the text inside follows --v-theme-on-surface → unreadable. -->
  <v-system-bar window color="background" absolute class="app-system-bar">
    <ParentBackButton />
    <div class="text-caption font-weight-bold title-bar flex-grow-1">
      <span class="text-caption">{{ currentTitle }}</span>
    </div>
    <!-- 帮助与反馈。**它现在是一个菜单**（`HelpAndFeedbackMenu`），桌面和手机共用：
         底下的「我的反馈」和「管理后台」今天只能二级跳，收进菜单之后三个目的地都是一次
         可达；入口本身也从「反馈」变成「帮助与反馈」—— 需求方原话是那两个字太不显眼。
         代价是直达反馈中心多一次点击，取舍写在那个组件的文件头里。
         登录与否都显示：没登录的人遇到的问题同样值得记下来（未读点那时画不出来，
         因为计数要登录）。 -->
    <HelpAndFeedbackMenu />
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

import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'
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

const currentTitle = ref(t('global.cheese'))

const updateTitle = () => {
  const hierarchy = getRouteHierarchy.value
  for (const item of hierarchy) {
    if (item.meta.isFullPage) {
      currentTitle.value = item.title
      return
    }
  }
  currentTitle.value = t('global.cheese')
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

/* 高度**钉死，不靠内容撑**。这个 chip 原来只有 `min-height`，实际高度是行盒 +
   上下内边距 + 上下描边算出来的（12px 的字在这条栏里落在 20px 的行盒上，20+2+2+1+1
   = 26），而旁边那条 `:size="24"` 的反馈入口是实打实的 24 —— 同一簇里两个控件差 2px，
   在真浏览器里量出来就是 24 对 26。行盒是被继承下来的，改一次字体就再差一次，所以
   这里把两边都定成 24：`height` 是 border-box，行盒放得下就不再参与高度。
   （端到端那条 `shell-default.spec.ts` 的「顶栏的反馈入口」钉的就是这个等式。） */
.app-system-bar .language-toggle {
  min-height: 24px;
  height: 24px;
  padding: 0 8px;
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
