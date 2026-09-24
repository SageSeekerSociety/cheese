<template>
  <!-- 「我」的菜单。桌面（左栏底部头像）和手机（顶栏头像）共用这一份：两处各写
       一遍的时候，手机那份就漏掉了「我的设备」。 -->
  <v-card class="user-menu-card pa-0" rounded="lg" min-width="300">
    <v-card-item class="pa-4 pb-3">
      <v-avatar
        size="56"
        class="mb-2"
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
      <div class="t-title text-truncate">{{ menu.nickname.value }}</div>
      <div class="t-body c-muted text-truncate">{{ menu.intro.value || '暂无个人简介' }}</div>
      <div class="t-meta-read t-num mt-1">UID {{ menu.currentUser.value?.id }}</div>
    </v-card-item>

    <v-divider />

    <v-list class="user-menu-list pa-2" bg-color="transparent">
      <v-list-item :to="{ name: 'UserDefault', params: { id: menu.currentUser.value?.id } }" rounded="lg" class="mb-1">
        <template #prepend>
          <v-icon icon="mdi-account" class="me-2"></v-icon>
        </template>
        <v-list-item-title>个人中心</v-list-item-title>
      </v-list-item>
      <v-list-item :to="{ name: 'my-devices' }" rounded="lg" class="mb-1">
        <template #prepend>
          <v-icon icon="mdi-server-network" class="me-2"></v-icon>
        </template>
        <v-list-item-title>我的设备</v-list-item-title>
      </v-list-item>
      <ThemeToggle />
      <v-list-item to="/about" rounded="lg" class="mb-1">
        <template #prepend>
          <v-icon icon="mdi-information-outline" class="me-2"></v-icon>
        </template>
        <v-list-item-title>了解知是</v-list-item-title>
      </v-list-item>
      <v-list-item rounded="lg" color="error" @click="menu.onLogout">
        <template #prepend>
          <v-icon icon="mdi-exit-to-app" class="me-2"></v-icon>
        </template>
        <v-list-item-title>退出登录</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-card>
</template>

<script setup lang="ts">
import type { useUserMenu } from '@/composables/useUserMenu'

import ThemeToggle from '@/components/common/ThemeToggle.vue'

defineProps<{ menu: ReturnType<typeof useUserMenu> }>()
</script>

<style scoped>
/* 菜单浮在页面之上，所以它（而不是卡片）可以有投影。 */
.user-menu-card {
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-2);
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
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
}

/* 选中与悬停都是中性色，同 TopicSidebar 的 .nav-row：琥珀在导航里只留给左栏
   那一格「当前在哪」。 */
.user-menu-list .v-list-item {
  min-height: 44px;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.user-menu-list .v-list-item:hover {
  background: var(--fill);
}

.user-menu-list .v-list-item--active {
  background: var(--line-2);
}

.user-menu-list .v-list-item--active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}

.user-menu-list .v-list-item--active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}

.user-menu-list .v-list-item--active :deep(.v-icon) {
  color: var(--muted);
}
</style>
