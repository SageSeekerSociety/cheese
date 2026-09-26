<template>
  <!-- 「我」的菜单。桌面（左栏底部头像）和手机（顶栏头像）共用这一份：两处各写
       一遍的时候，手机那份就漏掉了「我的设备」。
       四段：我是谁 / 我的东西 / 我的偏好 / 退出。「了解知是」讲的是产品而不是我，
       住在「帮助与反馈」菜单里。 -->
  <v-card class="user-menu-card pa-0" rounded="lg" width="280">
    <div class="user-menu-head">
      <v-avatar
        size="40"
        rounded="lg"
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

    <v-list class="menu-list" nav density="compact" bg-color="transparent">
      <v-list-item :to="{ name: 'UserPage', params: { handle: menu.currentUser.value?.username } }">
        <v-list-item-title>{{ t('navigation.userMenu.profile') }}</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'UserSettingsProfile' }">
        <v-list-item-title>{{ t('navigation.userMenu.settings') }}</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'my-devices' }">
        <v-list-item-title>{{ t('navigation.userMenu.devices') }}</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'my-connections' }">
        <v-list-item-title>{{ t('navigation.userMenu.connections') }}</v-list-item-title>
      </v-list-item>
    </v-list>

    <v-divider />

    <div class="user-menu-prefs">
      <ThemeToggle />
      <LanguagePreference />
    </div>

    <v-divider />

    <v-list class="menu-list" nav density="compact" bg-color="transparent">
      <v-list-item class="user-menu-logout" @click="menu.onLogout">
        <v-list-item-title>{{ t('navigation.userMenu.logout') }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-card>
</template>

<script setup lang="ts">
import type { useUserMenu } from '@/composables/useUserMenu'

import LanguagePreference from '@/components/common/LanguagePreference.vue'
import ThemeToggle from '@/components/common/ThemeToggle.vue'
import { t } from '@/i18n'

defineProps<{ menu: ReturnType<typeof useUserMenu> }>()
</script>

<style scoped>
/* 菜单浮在页面之上，所以它（而不是卡片）可以有投影。 */
.user-menu-card {
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-2);
}

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
   和列表项（style.css 的 .menu-list）一样是 36px。 */
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
