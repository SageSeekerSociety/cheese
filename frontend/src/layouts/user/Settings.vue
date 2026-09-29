<template>
  <!-- 手机上没有左边那条侧栏（SettingsSidebar 只在桌面画），这几块在内容上方排成
       一行标签，和项目文档那一页的标签同一个样子。 -->
  <nav v-if="!mdAndUp" class="settings-sections">
    <v-tabs :model-value="route.name" density="compact" color="on-surface" slider-color="primary">
      <v-tab
        v-for="section in SETTINGS_SECTIONS"
        :key="section.route.name"
        :value="section.route.name"
        :to="section.route"
        class="text-none"
        >{{ section.label() }}</v-tab
      >
    </v-tabs>
  </nav>
  <router-view />
</template>

<script lang="ts" setup>
import type { User } from '@/types/users'

import { onMounted, provide, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'

import { UserApi } from '@/network/api/users'
import { currentUserId } from '@/services/account'
import { SETTINGS_SECTIONS } from '@/views/user/settings/sections'

const route = useRoute()
const { mdAndUp } = useDisplay()
const userData = ref<User>()
const loaded = ref(false)

const fetchData = async () => {
  if (!currentUserId.value) {
    loaded.value = true
    return
  }
  const {
    data: { user },
  } = await UserApi.getUserInfo(currentUserId.value)
  userData.value = user
  loaded.value = true
}

onMounted(async () => {
  await fetchData()
})

provide('userData', userData)
</script>

<style scoped>
/* 左右和下面各页的内容对齐：页面自己在 600 以下留 16，以上留 32（settings-card.css）。 */
.settings-sections {
  max-width: calc(var(--page-w) - 64px);
  margin: 8px 32px 0;
  border-bottom: 1px solid var(--line);
}
@media (width < 600px) {
  .settings-sections {
    margin-inline: 16px;
  }
}
</style>
