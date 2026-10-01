<template>
  <!-- 「我」的菜单。桌面（左栏底部头像）和手机（顶栏头像）共用这一份：两处各写
       一遍的时候，手机那份就漏掉了一项。
       四段：我是谁 / 我的东西 / 我的偏好 / 退出。「了解知是」讲的是产品而不是我，
       住在「帮助与反馈」菜单里。 -->
  <v-card class="pa-0" width="280">
    <div class="user-menu-head">
      <v-avatar
        size="40"
        rounded="circle"
        :style="menu.avatar.value ? undefined : { backgroundColor: menu.avatarColor.value }"
      >
        <v-img v-if="menu.avatar.value" :src="menu.avatar.value">
          <template #error>
            <span class="user-menu-avatar-char" :style="{ backgroundColor: menu.avatarColor.value }">{{
              menu.avatarInitial.value
            }}</span>
          </template>
        </v-img>
        <span v-else class="user-menu-avatar-char">{{ menu.avatarInitial.value }}</span>
      </v-avatar>
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

/* 没挑过头像时的彩色首字母，同 LeftAppRail 的 .rail-avatar-char。 */
.user-menu-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  /* stylelint-disable-next-line color-no-hex -- 压在 avatarColor() 算出来的底色
     上的墨色。那个底色按固定感知亮度取（OKLCH L = 0.54），深浅两套主题下是同一个
     值，所以字也必须是同一个值；改成 token 反而会在两套主题里各错一次。 */
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
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
