<template>
  <!-- `background` (not a fixed grey): the title bar frames every page, so a
       Material palette name like grey-lighten-5 would pin it to #FAFAFA in dark
       theme while the text inside follows --v-theme-on-surface → unreadable. -->
  <v-system-bar window color="background" absolute class="app-system-bar">
    <ParentBackButton />
    <div class="text-caption font-weight-bold title-bar flex-grow-1">
      <span class="text-caption">{{ currentTitle }}</span>
    </div>
    <!-- 反馈入口。它是一条**路由**不是一个弹窗：反馈中心是一个完整的页面
         （有搜索、Tab、详情页、可以分享出去的链接），塞进浮层里这四件事一件都做不
         了。登录与否都显示——没登录的人遇到的问题同样值得记下来。

         `variant="outlined"` 画的边跟着 currentColor 走，也就是这个按钮本来就在用的
         `--muted`（见下面那条 CSS）。所以右边这一簇里它成了唯一有可见轮廓的控件，而
         紧随其后的语言开关仍然只有一条更淡的 `--line`——顺序正好和以前反过来，以前
         是「切换界面语言」比「给平台提意见」显眼。**一个琥珀色都没加**：这条入口不
         抢主操作位。
         图标用 `<template #prepend>` 显式给 14px，不用 `prepend-icon` prop——后者由
         Vuetify 按按钮尺寸推导，配 12px 文案会偏大。去掉 `title`：按钮已经有可见
         文案，那个 tooltip 只是把标签再念一遍。 -->
    <v-btn class="feedback-entry" variant="outlined" color="on-surface-variant" to="/feedback" aria-label="反馈">
      <template #prepend>
        <v-icon size="14" aria-hidden="true">mdi-comment-quote-outline</v-icon>
      </template>
      反馈
    </v-btn>
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

/* 和语言开关同一档高度：这条系统栏里的东西高度必须一致，否则整条会看起来参差。
   高度写在这里，**不用 `:size="24"`** —— 那个 prop 对数字给的是「一个方格」：
   `useSize` 同时下发内联的 `width` 和 `height`，于是这颗**带文字**的按钮被压成
   24×24，而里面的「图标 + 反馈」有 25px 宽，`overflow` 又是 visible：字直接压到右边
   那颗语言开关上（在真浏览器里量到过：按钮右沿 1346、内容右沿 1353）。语言开关是
   `icon` 按钮，方格正是它要的；这一颗要的是「高 24、宽随内容」。 */
.app-system-bar .feedback-entry {
  height: 24px;
  padding: 0 10px;
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
