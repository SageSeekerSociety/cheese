<template>
  <!-- 「我」的菜单。桌面（左栏底部头像）和手机（顶栏头像）共用这一份：两处各写
       一遍的时候，手机那份就漏掉了一项。
       四段：我是谁 / 我的东西 / 我的偏好 / 退出。「了解知是」讲的是产品而不是我，
       住在「帮助与反馈」菜单里。 -->
  <v-card class="pa-0" width="280">
    <div class="user-menu-head">
      <!-- 头像走 UserAvatar：挑过就画那张，没挑过/取不到就画按 handle 派生的彩色首字母，
           失败记忆也归它一处管。以前这里自画一份，和 nav 左栏、顶栏各画一份，同一个
           人的头像会三处三种样子。 -->
      <UserAvatar
        :avatar="menu.avatar.value ?? ''"
        :name="menu.nickname.value"
        :seed="menu.currentUser.value?.username"
        :size="40"
      />
      <div class="user-menu-head__text">
        <div class="t-title text-truncate">{{ menu.nickname.value }}</div>
        <div class="user-menu-head__intro text-truncate">
          {{ menu.intro.value || t('navigation.userMenu.noIntro') }}
        </div>
        <div class="t-meta-read t-num">UID {{ menu.currentUser.value?.id }}</div>
      </div>
    </div>

    <v-divider />

    <v-list>
      <v-list-item :to="{ name: 'UserPage', params: { handle: menu.currentUser.value?.username } }">
        <v-list-item-title>{{ t('navigation.userMenu.profile') }}</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'UserSettings' }">
        <v-list-item-title>{{ t('navigation.userMenu.settings') }}</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'my-archived-projects' }">
        <v-list-item-title>{{ t('navigation.userMenu.archivedProjects') }}</v-list-item-title>
      </v-list-item>
      <!-- 桌面 app 里已经装好了，下载页剩下有意义的只有手机那一块：一个扫码的对话框。 -->
      <v-list-item v-if="inApp" @click="phoneOpen = true">
        <v-list-item-title>{{ t('navigation.userMenu.usePhone') }}</v-list-item-title>
      </v-list-item>
      <v-list-item v-else :to="{ name: 'Download' }">
        <v-list-item-title>{{ t('navigation.userMenu.download') }}</v-list-item-title>
      </v-list-item>
    </v-list>

    <v-divider />

    <div class="user-menu-prefs">
      <ThemeToggle />
      <LanguagePreference />
    </div>

    <v-divider />

    <v-list>
      <v-list-item class="user-menu-logout" @click="menu.onLogout">
        <v-list-item-title>{{ t('navigation.userMenu.logout') }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-card>
</template>

<script setup lang="ts">
import type { useUserMenu } from '@/composables/useUserMenu'

import { useDesktopApp } from '@/composables/useDesktopApp'

import LanguagePreference from '@/components/common/LanguagePreference.vue'
import ThemeToggle from '@/components/common/ThemeToggle.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { inDesktopApp } from '@/lib/desktopApp'

defineProps<{ menu: ReturnType<typeof useUserMenu> }>()

const inApp = inDesktopApp()
const { phoneOpen } = useDesktopApp()
</script>

<style scoped>
.user-menu-head {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 16px;
}

.user-menu-head__text {
  min-width: 0;
}

.user-menu-head__intro {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* 退出不是破坏性操作，不用红；它只是这张菜单里最不常用的一项，用次要文字色。 */
.user-menu-logout .v-list-item-title {
  color: var(--muted);
}

.user-menu-prefs {
  padding: 4px 0;
}

/* 偏好行（ThemeToggle / LanguagePreference）：左边是名字，右边是分段控件，高度
   和菜单里的列表项（style.css 的浮层规则）一样是 36px。 */
.user-menu-prefs :deep(.pref-row) {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
  padding: 0 16px;
}

.user-menu-prefs :deep(.pref-row__label) {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
</style>
